from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import TypeAdapter

from hames.gateway import GatewayState, create_app
from hames.inspection import inspect_run
from hames.ledger import Ledger
from hames.memory import MemoryCandidate
from hames.paths import HamesPaths
from hames.plans import PLAN_READY_MARKER
from hames.providers import (
    ModelRequest,
    Provider,
    ProviderModel,
    StreamEvent,
    StreamEventKind,
    ToolCallDelta,
    Usage,
)
from hames.providers.base import JSON_OBJECT, JsonValue
from hames.providers.fake import FakeProvider
from hames.skills import SkillDraft
from hames.workspaces import Workspace

EVENT_LIST = TypeAdapter(list[dict[str, JsonValue]])


def response_object(response: httpx.Response) -> dict[str, JsonValue]:
    return JSON_OBJECT.validate_python(cast(object, response.json()))


def _user_contents(events: list[dict[str, JsonValue]]) -> list[str]:
    contents: list[str] = []
    for event in events:
        if event["type"] != "user.message":
            continue
        payload = JSON_OBJECT.validate_python(event["payload"])
        content = payload["content"]
        assert isinstance(content, str)
        contents.append(content)
    return contents


class ForegroundOverlapProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.second_started = asyncio.Event()
        self.release_second = asyncio.Event()

    async def list_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="fixture",
                provider="fake",
                status="available",
                input_modalities=["text"],
                output_modalities=["text"],
            )
        ]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        if len(self.requests) == 2:
            self.second_started.set()
            await self.release_second.wait()
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="done")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    async def aclose(self) -> None:
        return None


class VisionFakeProvider(FakeProvider):
    async def list_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="fixture",
                provider="fake",
                status="available",
                input_modalities=["text", "image"],
                output_modalities=["text"],
            )
        ]


@pytest.mark.asyncio
async def test_workspace_crud_directory_browser_and_scoped_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            added = await client.post("/v1/workspaces", headers=headers, json={"path": str(first)})
            assert added.status_code == 201
            workspace = response_object(added)
            assert workspace["title"] == "first"
            assert workspace["available"] is True

            first_session = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(first), "provider": "fake", "model": "fixture"},
            )
            first_session_id = str(response_object(first_session)["id"])
            trusted_after_add = await client.get(
                f"/v1/sessions/{first_session_id}/trust", headers=headers
            )
            assert response_object(trusted_after_add)["trusted"] is True

            renamed = await client.patch(
                f"/v1/workspaces/{workspace['id']}",
                headers=headers,
                json={"title": "First project"},
            )
            assert response_object(renamed)["title"] == "First project"

            listing = await client.get(
                "/v1/directories", headers=headers, params={"path": str(tmp_path)}
            )
            assert listing.status_code == 200
            assert {entry["name"] for entry in listing.json()["directories"]} >= {
                "first",
                "second",
            }

            created_folder = await client.post(
                "/v1/directories",
                headers=headers,
                json={"parent": str(tmp_path), "name": "third"},
            )
            assert created_folder.status_code == 201
            assert (tmp_path / "third").is_dir()
            created_folder_session = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path / "third"),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            created_folder_trust = await client.get(
                f"/v1/sessions/{created_folder_session.json()['id']}/trust", headers=headers
            )
            assert response_object(created_folder_trust)["trusted"] is True

            def select_second(_: Path | None) -> Path:
                return second

            before_selection = state.workspaces.list()
            monkeypatch.setattr(state.workspaces, "select_directory", select_second)
            selected_folder = await client.post(
                "/v1/directories/select",
                headers=headers,
                json={"initial_path": str(first)},
            )
            assert selected_folder.status_code == 200
            assert response_object(selected_folder)["path"] == str(second)
            assert state.workspaces.list() == before_selection
            named_folder = await client.post(
                "/v1/workspaces",
                headers=headers,
                json={"path": str(second), "title": "Named project"},
            )
            assert named_folder.status_code == 201
            assert response_object(named_folder)["title"] == "Named project"

            def pick_second(_: Path | None) -> Workspace:
                return state.workspaces.register(second, touch=False)

            monkeypatch.setattr(state.workspaces, "pick_directory", pick_second)
            picked_folder = await client.post(
                "/v1/directories/pick",
                headers=headers,
                json={"initial_path": str(first)},
            )
            assert picked_folder.status_code == 200
            assert response_object(picked_folder)["path"] == str(second)

            second_session = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(second),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            scoped = await client.get(
                "/v1/sessions",
                headers=headers,
                params={"working_directory": str(first)},
            )
            assert [session["id"] for session in scoped.json()] == [first_session.json()["id"]]
            assert second_session.json()["id"] not in {session["id"] for session in scoped.json()}

            deleted = await client.delete(f"/v1/workspaces/{workspace['id']}", headers=headers)
            assert deleted.status_code == 200
            assert first.is_dir()
            assert first_session.json()["id"] in {
                session["id"]
                for session in (await client.get("/v1/sessions", headers=headers)).json()
            }
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_explicit_workspace_add_reports_trust_persistence_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))

    def fail_grant(_: Path) -> object:
        raise OSError("database temporarily unavailable")

    monkeypatch.setattr(state.controls, "grant_trust", fail_grant)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/workspaces", headers=headers, json={"path": str(workspace_path)}
            )
            assert response.status_code == 500
            body = response_object(response)
            error = body["error"]
            assert isinstance(error, dict)
            assert error["code"] == "workspace_trust_persistence_failed"
            assert "trust grant could not be persisted" in str(error["message"])
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_session_environment_endpoint_exposes_current_workspace(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])

            response = await client.get(f"/v1/sessions/{session_id}/environment", headers=headers)

            assert response.status_code == 200
            environment = response_object(response)
            workspace = JSON_OBJECT.validate_python(environment["workspace"])
            assert workspace["cwd"] == str(tmp_path)
            assert workspace["repository"] is False
            assert environment["tool_shell"] == "/bin/bash"
            assert environment["tool_terminal"] == "noninteractive"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_message_image_is_durable_and_reaches_vision_provider(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = VisionFakeProvider(
        [
            StreamEvent(kind=StreamEventKind.STARTED),
            StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="seen"),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]
    )
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    encoded = "iVBORw0KGgpyZXN0"
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)

            sent = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "content": "What is this?",
                    "attachments": [
                        {"name": "pixel.png", "media_type": "image/png", "data_base64": encoded}
                    ],
                },
            )
            assert sent.status_code == 202
            await _wait_for_event(client, headers, session_id, "run.completed")
            assert provider.requests[-1].messages[-1].attachments[0].data_base64 == encoded

            user_event = next(
                event
                for event in state.ledger.list_events(session_id)
                if event.type == "user.message"
            )
            references = cast(list[object], user_event.payload["attachments"])
            reference = JSON_OBJECT.validate_python(references[0])
            digest = str(reference["digest"])
            assert "data_base64" not in reference
            fetched = await client.get(
                f"/v1/sessions/{session_id}/attachments/{digest}", headers=headers
            )
            assert fetched.status_code == 200
            assert fetched.content.startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_session_list_can_filter_empty_sessions(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            empty = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            populated = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            empty_id = str(response_object(empty)["id"])
            populated_id = str(response_object(populated)["id"])
            state.ledger.append(
                session_id=populated_id,
                event_type="user.message",
                payload={"content": "hello"},
            )

            response = await client.get(
                "/v1/sessions", headers=headers, params={"has_messages": "true"}
            )

            assert response.status_code == 200
            assert [session["id"] for session in response.json()] == [populated_id]
            assert empty_id not in {session["id"] for session in response.json()}
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_tool_result_details_resolve_only_the_event_retained_blob(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        session = state.ledger.create_session(
            working_directory=tmp_path,
            agent_id="default",
            provider="fake",
            model="fixture",
        )
        full = "--- a/file\n+++ b/file\n@@ -1 +1 @@\n" + "+expanded detail\n" * 70_000
        digest = state.ledger.blob_store.put(full.encode())
        result = state.ledger.append(
            session_id=session.id,
            event_type="tool.completed",
            payload={
                "tool_call_id": "call-details",
                "name": "edit_file",
                "status": "completed",
                "summary": "edited file",
                "content": full[:100] + "\n[output truncated]",
                "truncated": True,
                "blob_references": [digest],
            },
        )
        inline_result = state.ledger.append(
            session_id=session.id,
            event_type="tool.completed",
            payload={
                "tool_call_id": "call-inline",
                "name": "edit_file",
                "status": "completed",
                "summary": "edited small file",
                "content": "--- a/small\n+++ b/small\n@@ -1 +1 @@\n-old\n+new\n",
            },
        )
        missing_result = state.ledger.append(
            session_id=session.id,
            event_type="tool.completed",
            payload={
                "tool_call_id": "call-missing",
                "name": "edit_file",
                "status": "completed",
                "summary": "edited missing file",
                "content": "[output truncated]",
                "truncated": True,
                "blob_references": ["0" * 64],
            },
        )
        ordinary = state.ledger.append(
            session_id=session.id,
            event_type="user.message",
            payload={"content": "hello"},
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/v1/events/{result.id}/tool-result-details", headers=headers
            )
            invalid = await client.get(
                f"/v1/events/{ordinary.id}/tool-result-details", headers=headers
            )
            inline = await client.get(
                f"/v1/events/{inline_result.id}/tool-result-details", headers=headers
            )
            missing = await client.get(
                f"/v1/events/{missing_result.id}/tool-result-details", headers=headers
            )

        assert response.status_code == 200
        details = response_object(response)
        assert details["event_id"] == result.id
        assert details["tool_call_id"] == "call-details"
        assert details["retained"] is True
        assert details["complete"] is False
        assert details["returned_chars"] == 1_048_576
        assert details["total_chars"] == len(full)
        assert str(details["content"]).startswith("--- a/file")
        assert inline.status_code == 200
        inline_details = response_object(inline)
        assert inline_details["complete"] is True
        assert inline_details["retained"] is False
        assert inline_details["content"] == inline_result.payload["content"]
        assert invalid.status_code == 409
        assert response_object(invalid)["error"]["code"] == "event_has_no_tool_result"  # type: ignore[index]
        assert missing.status_code == 409
        assert response_object(missing)["error"]["code"] == "tool_result_details_unavailable"  # type: ignore[index]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_message_submission_ids_replay_without_duplicate_runs_or_queue_entries(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = QueueProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)

            started_id = "11111111-1111-4111-8111-111111111111"
            payload = {"submission_id": started_id, "content": "only once"}
            first, duplicate = await asyncio.gather(
                client.post(f"/v1/sessions/{session_id}/messages", headers=headers, json=payload),
                client.post(f"/v1/sessions/{session_id}/messages", headers=headers, json=payload),
            )
            first_body = response_object(first)
            duplicate_body = response_object(duplicate)
            assert {bool(first_body["replayed"]), bool(duplicate_body["replayed"])} == {
                False,
                True,
            }
            assert first_body["submission_id"] == started_id
            assert duplicate_body["run_id"] == first_body["run_id"] == started_id
            assert (
                len(
                    [
                        event
                        for event in state.ledger.list_events(session_id)
                        if event.type == "user.message"
                        and event.payload.get("submission_id") == started_id
                    ]
                )
                == 1
            )

            conflict = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"submission_id": started_id, "content": "different"},
            )
            assert conflict.status_code == 409
            assert response_object(conflict)["error"]["code"] == "submission_id_reused"  # type: ignore[index]

            await asyncio.wait_for(provider.first_started.wait(), timeout=1)
            queued_id = "22222222-2222-4222-8222-222222222222"
            queued_payload = {"submission_id": queued_id, "content": "queued once"}
            queued = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json=queued_payload,
            )
            queued_replay = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json=queued_payload,
            )
            assert response_object(queued)["replayed"] is False
            assert response_object(queued_replay)["replayed"] is True
            queue = response_object(
                await client.get(f"/v1/sessions/{session_id}/queue", headers=headers)
            )
            assert [item["id"] for item in queue["items"]] == [queued_id]  # type: ignore[index]

            provider.release_first.set()
            await _wait_for_event(client, headers, session_id, "run.completed", occurrences=2)
            assert _user_contents(
                EVENT_LIST.validate_python(
                    (await client.get(f"/v1/sessions/{session_id}/events", headers=headers)).json()
                )
            ) == ["only once", "queued once"]
    finally:
        await state.runs.close()


class AccountUsageProvider:
    profile_id = "codex-fixture"
    adapter = "codex"
    base_url = "app-server://codex"

    async def list_models(self) -> list[ProviderModel]:
        return [ProviderModel(id="fixture", provider=self.profile_id, status="available")]

    async def account_rate_limits(self) -> dict[str, JsonValue]:
        return {
            "plan_type": "prolite",
            "weekly_window": {
                "used": 58,
                "remaining": 42,
                "reset_at": 2_000_000_000,
                "window_minutes": 10_080,
            },
        }

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        del request
        yield StreamEvent(kind=StreamEventKind.STARTED)
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="done")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    async def aclose(self) -> None:
        return None


class PlanExecutionProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self, plan_markdown: str, *, truncate_first_execution: bool = False) -> None:
        self.plan_markdown = plan_markdown
        self.truncate_first_execution = truncate_first_execution
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ProviderModel]:
        return [ProviderModel(id="fixture", provider="fake", status="available")]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        index = len(self.requests) - 1
        yield StreamEvent(kind=StreamEventKind.STARTED)
        if index == 0:
            yield StreamEvent(
                kind=StreamEventKind.TEXT_DELTA,
                text=f"{self.plan_markdown}\n\n{PLAN_READY_MARKER}",
            )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")
            return
        if self.truncate_first_execution and index == 1:
            yield StreamEvent(
                kind=StreamEventKind.REASONING_DELTA,
                text="I will begin with the first implementation task, then verify it.",
            )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="length")
            return
        tool_request_index = 2 if self.truncate_first_execution else 1
        if index == tool_request_index:
            task_ids = re.findall(r"- \[([^\]]+)\] pending:", request.system)
            assert task_ids
            for call_index, task_id in enumerate(task_ids):
                yield StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=call_index,
                        provider_call_id=f"task-{call_index}",
                        name="task_update",
                        arguments_delta=json.dumps(
                            {"action": "update", "task_id": task_id, "status": "completed"}
                        ),
                    ),
                )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")
            return
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Implemented and verified.")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    async def aclose(self) -> None:
        return None


class QuestionProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ProviderModel]:
        return [ProviderModel(id="fixture", provider="fake", status="available")]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        if len(self.requests) == 1:
            yield StreamEvent(
                kind=StreamEventKind.TOOL_CALL_DELTA,
                tool_call=ToolCallDelta(
                    index=0,
                    provider_call_id="question-1",
                    name="ask_user",
                    arguments_delta=json.dumps(
                        {
                            "question": "Which visual direction should I use?",
                            "options": [
                                {
                                    "label": "Subdued",
                                    "description": "Calm contrast.\n\nUse motion sparingly.",
                                },
                                {
                                    "label": "High contrast",
                                    "description": "Stronger separation between controls.",
                                },
                                {
                                    "label": "Terminal native",
                                    "description": "Favor the active terminal palette.",
                                },
                            ],
                        }
                    ),
                ),
            )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")
            return
        result = json.loads(request.messages[-1].content)
        assert result["structured_data"] == {
            "question_id": result["structured_data"]["question_id"],
            "answer": "Subdued\nNote: Keep it calm",
            "answer_type": "single_choice",
            "selected_option": "Subdued",
            "selected_description": "Calm contrast.\n\nUse motion sparingly.",
            "selected_options": ["Subdued"],
            "selected_descriptions": ["Calm contrast.\n\nUse motion sparingly."],
            "note": "Keep it calm",
            "custom": False,
        }
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="I will use the subdued direction.")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    async def aclose(self) -> None:
        return None


class QuestionKindsProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ProviderModel]:
        return [ProviderModel(id="fixture", provider="fake", status="available")]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        if len(self.requests) == 1:
            arguments = {
                "question": "Which checks should run?",
                "answer_type": "multiple_choice",
                "options": [
                    {"label": "Unit", "description": "Fast checks."},
                    {"label": "Integration", "description": "Gateway checks."},
                    {"label": "Browser", "description": "Rendered checks."},
                ],
                "min_selections": 2,
                "max_selections": 3,
            }
        elif len(self.requests) == 2:
            result = json.loads(request.messages[-1].content)
            assert result["structured_data"]["selected_options"] == ["Unit", "Browser"]
            assert result["structured_data"]["selected_descriptions"] == [
                "Fast checks.",
                "Rendered checks.",
            ]
            arguments = {
                "question": "What should the release be called?",
                "answer_type": "text",
                "placeholder": "Release name",
            }
        else:
            result = json.loads(request.messages[-1].content)
            assert result["structured_data"]["answer_type"] == "text"
            assert result["structured_data"]["answer"] == "Moonrise"
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="The checks and name are set.")
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")
            return
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call=ToolCallDelta(
                index=0,
                provider_call_id=f"question-{len(self.requests)}",
                name="ask_user",
                arguments_delta=json.dumps(arguments),
            ),
        )
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")

    async def aclose(self) -> None:
        return None


class StalledPlanExecutionProvider(PlanExecutionProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        if len(self.requests) == 1:
            yield StreamEvent(
                kind=StreamEventKind.TEXT_DELTA,
                text=f"{self.plan_markdown}\n\n{PLAN_READY_MARKER}",
            )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")
            return
        yield StreamEvent(kind=StreamEventKind.REASONING_DELTA, text="Still planning the work.")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="length")


class FalseBlockerProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ProviderModel]:
        return [ProviderModel(id="fixture", provider="fake", status="available")]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        if len(self.requests) == 1:
            arguments = {"action": "add", "text": "Create the requested file"}
        elif len(self.requests) == 2:
            task_ids = re.findall(r"- \[([^\]]+)\] pending:", request.system)
            assert task_ids
            arguments = {
                "action": "update",
                "task_id": task_ids[0],
                "status": "blocked",
                "blocked_reason": "current session filesystem is read-only",
                "text": (
                    "Create the requested file (blocked: current session filesystem is read-only)."
                ),
            }
        else:
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Continuing with Hames tools.")
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")
            return
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call=ToolCallDelta(
                index=0,
                provider_call_id=f"task-{len(self.requests)}",
                name="task_update",
                arguments_delta=json.dumps(arguments),
            ),
        )
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")

    async def aclose(self) -> None:
        return None


class QwenFakeProvider(FakeProvider):
    adapter = "llama_cpp"

    async def list_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="qwen3.8-27b",
                provider=self.profile_id,
                status="available",
                reasoning_supported=True,
                reasoning_efforts=["low", "medium", "xhigh"],
            )
        ]


class StructuredMessageProvider:
    profile_id = "structured"
    adapter = "codex"
    base_url = ""

    async def list_models(self) -> list[ProviderModel]:
        return [ProviderModel(id="fixture", provider=self.profile_id, status="available")]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(kind=StreamEventKind.STARTED, provider_request_id="turn-1")
        yield StreamEvent(
            kind=StreamEventKind.REASONING_DELTA,
            text="Inspect the workspace.",
            provider_item_id="reasoning-1",
        )
        yield StreamEvent(
            kind=StreamEventKind.REASONING_COMPLETED,
            text="Inspect the workspace.",
            provider_item_id="reasoning-1",
        )
        yield StreamEvent(
            kind=StreamEventKind.TEXT_DELTA,
            text="I'll inspect this first.",
            provider_item_id="commentary-1",
        )
        yield StreamEvent(
            kind=StreamEventKind.TEXT_COMPLETED,
            text="I'll inspect this first.",
            provider_item_id="commentary-1",
            message_phase="commentary",
        )
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call=ToolCallDelta(
                index=0,
                provider_call_id="call-1",
                name="list_dir",
                arguments_delta=json.dumps({"path": "."}),
            ),
        )
        assert request.tool_handler is not None
        result = await request.tool_handler("list_dir", {"path": "."}, "call-1")
        assert result.status == "completed"
        yield StreamEvent(
            kind=StreamEventKind.REASONING_DELTA,
            text="The evidence is sufficient.",
            provider_item_id="reasoning-2",
        )
        yield StreamEvent(
            kind=StreamEventKind.REASONING_COMPLETED,
            text="The evidence is sufficient.",
            provider_item_id="reasoning-2",
        )
        yield StreamEvent(
            kind=StreamEventKind.TEXT_DELTA,
            text="Final answer only.",
            provider_item_id="answer-1",
        )
        yield StreamEvent(
            kind=StreamEventKind.TEXT_COMPLETED,
            text="Final answer only.",
            provider_item_id="answer-1",
            message_phase="final_answer",
        )
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    async def aclose(self) -> None:
        return None


class BlockingPostRunObserver:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def observe_run(self, _session_id: str, _run_id: str) -> None:
        self.started.set()
        await self.release.wait()


class QueueProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def list_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="fixture",
                provider="fake",
                status="available",
                input_modalities=["text"],
                output_modalities=["text"],
            )
        ]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        if len(self.requests) == 1:
            self.first_started.set()
            await self.release_first.wait()
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="done")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    async def aclose(self) -> None:
        return None


class PlanRevisionProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def list_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="fixture",
                provider="fake",
                status="available",
                input_modalities=["text"],
                output_modalities=["text"],
            )
        ]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        if len(self.requests) == 1:
            self.first_started.set()
            await self.release_first.wait()
            text = f"# Initial plan\n\n## Tasks\n- [ ] First step\n\n{PLAN_READY_MARKER}"
        else:
            text = f"# Revised plan\n\n## Tasks\n- [ ] Revised step\n\n{PLAN_READY_MARKER}"
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text=text)
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    async def aclose(self) -> None:
        return None


class GoalForegroundProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.goal_started = asyncio.Event()
        self.never_release = asyncio.Event()

    async def list_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="fixture",
                provider="fake",
                status="available",
                input_modalities=["text"],
                output_modalities=["text"],
            )
        ]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        turn = len(self.requests)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        if turn == 1:
            self.goal_started.set()
            await self.never_release.wait()
        elif turn == 3:
            yield StreamEvent(
                kind=StreamEventKind.TOOL_CALL_DELTA,
                tool_call=ToolCallDelta(
                    index=0,
                    provider_call_id="goal-achieved-after-foreground",
                    name="goal_report",
                    arguments_delta=json.dumps(
                        {
                            "status": "achieved",
                            "summary": "Goal completed after foreground request",
                            "evidence": ["foreground request was handled first"],
                        }
                    ),
                ),
            )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")
            return
        else:
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="done")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_gateway_queues_three_messages_and_promotes_them_fifo(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = QueueProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)

            started = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "one"},
            )
            assert response_object(started)["disposition"] == "started"
            await asyncio.wait_for(provider.first_started.wait(), timeout=1)

            for content, position in [("two", 1), ("three", 2), ("four", 3)]:
                queued = await client.post(
                    f"/v1/sessions/{session_id}/messages",
                    headers=headers,
                    json={"content": content},
                )
                body = response_object(queued)
                assert body["disposition"] == "queued"
                assert body["queued"]["position"] == position  # type: ignore[index]

            full = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "five"},
            )
            assert full.status_code == 409
            assert response_object(full)["error"]["code"] == "session_queue_full"  # type: ignore[index]

            queue_before = (
                await client.get(f"/v1/sessions/{session_id}/queue", headers=headers)
            ).json()
            edit_id = queue_before["items"][1]["id"]
            edited = await client.patch(
                f"/v1/sessions/{session_id}/queue/{edit_id}",
                headers=headers,
                json={"content": "three edited", "expected_content": "three"},
            )
            assert edited.status_code == 200
            assert [item["id"] for item in edited.json()["items"]] == [
                item["id"] for item in queue_before["items"]
            ]
            stale = await client.patch(
                f"/v1/sessions/{session_id}/queue/{edit_id}",
                headers=headers,
                json={"content": "stale edit", "expected_content": "three"},
            )
            assert stale.status_code == 409

            provider.release_first.set()
            events = await _wait_for_event(
                client, headers, session_id, "run.completed", occurrences=4
            )
            users = _user_contents(events)
            assert users == ["one", "two", "three edited", "four"]
            queue = await client.get(f"/v1/sessions/{session_id}/queue", headers=headers)
            assert response_object(queue)["items"] == []
            late = await client.patch(
                f"/v1/sessions/{session_id}/queue/{edit_id}",
                headers=headers,
                json={"content": "do not recreate", "expected_content": "three edited"},
            )
            assert late.status_code == 404
            assert any(event["type"] == "queue.updated" for event in events)

    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_send_now_interrupts_and_runs_a_priority_turn_without_dropping_queue(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = QueueProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "active"},
            )
            await asyncio.wait_for(provider.first_started.wait(), timeout=1)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "older queued"},
            )

            priority = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "send now", "send_now": True},
            )
            priority_body = response_object(priority)
            assert priority_body["disposition"] == "queued"
            assert priority_body["queued"]["position"] == 1  # type: ignore[index]

            await _wait_for_event(client, headers, session_id, "run.cancelled")
            events = await _wait_for_event(
                client, headers, session_id, "run.completed", occurrences=2
            )
            users = _user_contents(events)
            assert users == ["active", "send now", "older queued"]
            assert len(provider.requests) == 3
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_queued_message_can_be_sent_now_without_losing_older_queue_items(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = QueueProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "active"},
            )
            await asyncio.wait_for(provider.first_started.wait(), timeout=1)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "older queued"},
            )
            latest = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "latest queued"},
            )
            latest_id = str(response_object(latest)["queued"]["id"])  # type: ignore[index]

            promoted = await client.post(
                f"/v1/sessions/{session_id}/queue/{latest_id}/send-now",
                headers=headers,
            )
            promoted_body = response_object(promoted)
            assert promoted.status_code == 202
            assert promoted_body["disposition"] == "queued"
            assert promoted_body["queued"]["id"] == latest_id  # type: ignore[index]
            assert promoted_body["queued"]["position"] == 1  # type: ignore[index]

            events = await _wait_for_event(
                client, headers, session_id, "run.completed", occurrences=2
            )
            assert _user_contents(events) == ["active", "latest queued", "older queued"]
            assert any(event["type"] == "queue.prioritized" for event in events)
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_paused_queue_survives_cancellation_until_explicit_resume(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = QueueProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)

            started = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "active"},
            )
            run_id = str(response_object(started)["run_id"])
            await asyncio.wait_for(provider.first_started.wait(), timeout=1)
            queued = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "keep me"},
            )
            queue_id = str(response_object(queued)["queued"]["id"])  # type: ignore[index]

            paused = await client.post(f"/v1/sessions/{session_id}/queue/pause", headers=headers)
            assert response_object(paused)["paused"] is True
            assert (
                await client.post(f"/v1/runs/{run_id}/cancel", headers=headers)
            ).status_code == 200
            await _wait_for_event(client, headers, session_id, "run.cancelled")
            await asyncio.sleep(0.02)

            still_queued = response_object(
                await client.get(f"/v1/sessions/{session_id}/queue", headers=headers)
            )
            assert still_queued["paused"] is True
            assert still_queued["items"][0]["id"] == queue_id  # type: ignore[index]
            assert len(provider.requests) == 1

            resumed = await client.post(f"/v1/sessions/{session_id}/queue/resume", headers=headers)
            assert response_object(resumed)["paused"] is False
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            users = _user_contents(events)
            assert users == ["active", "keep me"]
            assert len(provider.requests) == 2
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_usage_includes_codex_account_progress_when_available(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    providers: dict[str, Provider] = {"codex-fixture": AccountUsageProvider()}
    state = GatewayState.create(paths, providers=providers)
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "codex-fixture",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            usage = response_object(
                await client.get(f"/v1/sessions/{session_id}/usage", headers=headers)
            )
            account = usage["account_rate_limits"]
            assert isinstance(account, dict)
            assert account["plan_type"] == "prolite"
            weekly = account["weekly_window"]
            assert isinstance(weekly, dict)
            assert weekly["used"] == 58
            assert weekly["remaining"] == 42
            pooled = response_object(await client.get("/v1/usage", headers=headers))
            pooled_account = pooled["account_rate_limits"]
            assert isinstance(pooled_account, dict)
            assert pooled_account["plan_type"] == "prolite"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_pooled_usage_combines_activity_from_every_workspace(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    fake = FakeProvider(
        [
            StreamEvent(kind=StreamEventKind.STARTED),
            StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="done"),
            StreamEvent(kind=StreamEventKind.USAGE, usage=Usage(input_tokens=10, output_tokens=2)),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for index in range(2):
                workspace = tmp_path / f"workspace-{index}"
                workspace.mkdir()
                created = await client.post(
                    "/v1/sessions",
                    headers=headers,
                    json={
                        "working_directory": str(workspace),
                        "provider": "fake",
                        "model": "fixture",
                    },
                )
                session_id = str(response_object(created)["id"])
                await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
                await client.post(
                    f"/v1/sessions/{session_id}/messages",
                    headers=headers,
                    json={"content": f"turn {index}"},
                )
                await _wait_for_event(client, headers, session_id, "run.completed")

            pooled = response_object(await client.get("/v1/usage", headers=headers))
            assert pooled["input_tokens"] == 20
            assert pooled["output_tokens"] == 4
            assert pooled["model_requests"] == 2
            activity = pooled["daily_activity"]
            assert isinstance(activity, list)
            assert len(activity) == 1
            assert activity[0]["input_tokens"] == 20  # type: ignore[index]
            assert activity[0]["output_tokens"] == 4  # type: ignore[index]
            assert activity[0]["model_requests"] == 2  # type: ignore[index]
    finally:
        await state.runs.close()


class SlowAccountUsageProvider(AccountUsageProvider):
    async def account_rate_limits(self) -> dict[str, JsonValue]:
        await asyncio.sleep(30)
        return await super().account_rate_limits()


class ActiveAccountUsageProvider(AccountUsageProvider):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        del request
        yield StreamEvent(kind=StreamEventKind.STARTED)
        self.started.set()
        await self.release.wait()
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="done")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")


@pytest.mark.asyncio
async def test_usage_reads_codex_account_limits_during_an_active_turn(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = ActiveAccountUsageProvider()
    state = GatewayState.create(paths, providers={"codex-fixture": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "codex-fixture",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            submitted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "keep working"},
            )
            assert submitted.status_code == 202
            await asyncio.wait_for(provider.started.wait(), timeout=1)
            assert state.runs.is_session_active(session_id)

            usage = response_object(
                await client.get(f"/v1/sessions/{session_id}/usage", headers=headers)
            )
            account = usage["account_rate_limits"]
            assert isinstance(account, dict)
            weekly = account["weekly_window"]
            assert isinstance(weekly, dict)
            assert weekly["used"] == 58
            assert weekly["remaining"] == 42

            provider.release.set()
            await _wait_for_event(client, headers, session_id, "run.completed")
    finally:
        provider.release.set()
        await state.runs.close()


@pytest.mark.asyncio
async def test_usage_returns_session_totals_when_codex_account_metrics_hang(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"codex-fixture": SlowAccountUsageProvider()})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "codex-fixture",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            started = asyncio.get_running_loop().time()
            usage = response_object(
                await client.get(f"/v1/sessions/{session_id}/usage", headers=headers)
            )
            elapsed = asyncio.get_running_loop().time() - started
            assert elapsed < 5
            assert usage["model_requests"] == 0
            assert usage["account_rate_limits"] is None
            assert "timed out" in str(usage["account_rate_limits_error"])
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_runs_fake_conversation_with_durable_output(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [
            StreamEvent(kind=StreamEventKind.STARTED, provider_request_id="fixture-request"),
            StreamEvent(kind=StreamEventKind.REASONING_DELTA, text="check "),
            StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="hello"),
            StreamEvent(kind=StreamEventKind.USAGE, usage=Usage(input_tokens=10, output_tokens=2)),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/v1/health")
            assert health.status_code == 200
            health_body = response_object(health)
            assert health_body["protocol_version"] == 39
            assert health_body["provider_profiles"] == ["fake"]
            assert (await client.get("/v1/sessions")).status_code == 401

            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                    "reasoning_effort": "medium",
                },
            )
            assert created.status_code == 201
            session_id = str(response_object(created)["id"])
            recent = await client.get(
                "/v1/sessions/recent",
                headers=headers,
                params={"working_directory": str(tmp_path)},
            )
            assert recent.status_code == 200
            assert response_object(recent)["id"] == session_id
            assert (
                await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            ).status_code == 200
            invalid_paste = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "content": "é",
                    "paste_spans": [
                        {"start_byte": 1, "end_byte": 2, "line_count": 1, "byte_count": 1}
                    ],
                },
            )
            assert invalid_paste.status_code == 422
            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "content": "Hi",
                    "paste_spans": [
                        {"start_byte": 0, "end_byte": 2, "line_count": 1, "byte_count": 2}
                    ],
                },
            )
            assert accepted.status_code == 202, accepted.text
            run_id = str(response_object(accepted)["run_id"])

            event_types: list[str] = []
            events: list[dict[str, JsonValue]] = []
            for _ in range(100):
                response = await client.get(f"/v1/sessions/{session_id}/events", headers=headers)
                events = EVENT_LIST.validate_python(cast(object, response.json()))
                event_types = [str(event["type"]) for event in events]
                if "run.completed" in event_types:
                    break
                await asyncio.sleep(0.01)
            assert "session.title.changed" in event_types
            assert [kind for kind in event_types if kind != "session.title.changed"][:14] == [
                "session.opened",
                "trust.granted",
                "user.message",
                "run.started",
                "memory.retrieved",
                "skill.catalogued",
                "context.compiled",
                "model.requested",
                "model.response.started",
                "model.usage",
                "assistant.reasoning",
                "assistant.message",
                "model.response.completed",
                "run.completed",
            ]
            # Post-run memory and Skill observers run sequentially. Waiting for the
            # latter makes the inspection timeline deterministic on slower hosts.
            events = await _wait_for_event(client, headers, session_id, "skill.workflow.observed")
            reasoning = next(event for event in events if event["type"] == "assistant.reasoning")
            answer = next(event for event in events if event["type"] == "assistant.message")
            reasoning_payload = reasoning["payload"]
            answer_payload = answer["payload"]
            assert isinstance(reasoning_payload, dict)
            assert isinstance(answer_payload, dict)
            assert reasoning_payload["content"] == "check "
            duration = reasoning_payload["duration_seconds"]
            assert isinstance(duration, (int, float))
            assert duration >= 0
            assert answer_payload["content"] == "hello"
            user_message = next(event for event in events if event["type"] == "user.message")
            user_payload = user_message["payload"]
            assert isinstance(user_payload, dict)
            assert user_payload["paste_spans"] == [
                {"start_byte": 0, "end_byte": 2, "line_count": 1, "byte_count": 2}
            ]
            assert fake.requests[0].reasoning_effort == "medium"
            context_event = next(event for event in events if event["type"] == "context.compiled")
            context_payload = context_event["payload"]
            assert isinstance(context_payload, dict)
            snapshot_hash = str(context_payload["request_snapshot_blob_hash"])
            snapshot = state.ledger.blob_store.read(snapshot_hash)
            assert hashlib.sha256(snapshot).hexdigest() == context_payload["request_hash"]
            persisted_request = json.loads(snapshot)
            assert persisted_request["messages"][0]["content"] == "Hi"
            assert persisted_request["max_tokens"] == 16384

            runs_response = await client.get(f"/v1/sessions/{session_id}/runs", headers=headers)
            assert runs_response.status_code == 200
            runs = runs_response.json()
            assert runs[0]["run_id"] == run_id
            assert runs[0]["status"] == "completed"
            inspection_response = await client.get(f"/v1/runs/{run_id}/inspection", headers=headers)
            assert inspection_response.status_code == 200
            inspection = inspection_response.json()
            assert inspection["usage"]["input_tokens"] == 10
            assert inspection["usage"]["estimated_input_tokens"] > 0
            assert [item["channel"] for item in inspection["timeline"]] == [
                "user",
                "lifecycle",
                "memory",
                "skill",
                "context",
                "lifecycle",
                "lifecycle",
                "usage",
                "thinking",
                "answer",
                "lifecycle",
                "lifecycle",
                "memory",
                "skill",
            ]
            context_response = await client.get(
                f"/v1/contexts/{context_event['id']}", headers=headers
            )
            assert context_response.status_code == 200
            assert context_response.json()["request_snapshot"] == persisted_request
            usage_response = await client.get(f"/v1/sessions/{session_id}/usage", headers=headers)
            usage_body = usage_response.json()
            assert usage_body["input_tokens"] == 10
            assert usage_body["daily_activity"] == [
                {
                    "date": str(
                        next(event for event in events if event["type"] == "model.usage")[
                            "created_at"
                        ]
                    )[:10],
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "cached_input_tokens": 0,
                    "reasoning_tokens": 0,
                    "provider_reported_cost": 0.0,
                    "model_requests": 1,
                }
            ]
            assert usage_body["latest_context"] == {
                "provider": context_payload["provider"],
                "model": context_payload["model"],
                "agent_id": context_payload["agent_id"],
                "estimated_input_tokens": context_payload["estimated_input_tokens"],
                "context_window_tokens": context_payload["context_window_tokens"],
                "input_budget_tokens": context_payload["input_budget_tokens"],
                "output_reserve_tokens": context_payload["output_reserve_tokens"],
                "context_window_source": context_payload["context_window_source"],
            }
            markdown = await client.get(
                f"/v1/sessions/{session_id}/transcript",
                headers=headers,
            )
            assert markdown.status_code == 200
            assert "Derived view only" in markdown.text
            assert "private" not in markdown.text
            assert "check " in markdown.text
            jsonl = await client.get(
                f"/v1/sessions/{session_id}/transcript",
                headers=headers,
                params={"format": "jsonl"},
            )
            assert json.loads(jsonl.text.splitlines()[0])["provenance_authority"] == "event-ledger"

            titled = await client.put(
                f"/v1/sessions/{session_id}/title",
                headers=headers,
                json={"title": "  Durable   gateway conversation  "},
            )
            assert titled.status_code == 200
            assert response_object(titled)["title"] == "Durable gateway conversation"
            title_events = await _wait_for_event(
                client, headers, session_id, "session.title.changed"
            )
            title_event = next(
                event
                for event in reversed(title_events)
                if event["type"] == "session.title.changed"
            )
            assert title_event["payload"] == {"title": "Durable gateway conversation"}

            reopened = Ledger.open(paths.database)
            assert (
                inspect_run(reopened, run_id).model_dump()
                == inspect_run(state.ledger, run_id).model_dump()
            )

            branch = state.ledger.fork_session(session_id)
            branch_accepted = await client.post(
                f"/v1/sessions/{branch.id}/messages",
                headers=headers,
                json={"content": "Continue"},
            )
            assert branch_accepted.status_code == 202
            for _ in range(100):
                if len(fake.requests) == 2:
                    break
                await asyncio.sleep(0.01)
            assert [message.content for message in fake.requests[1].messages] == [
                "Hi",
                "hello",
                "Continue",
            ]

            forked = await client.post(
                f"/v1/sessions/{session_id}/fork",
                headers=headers,
                json={},
            )
            assert forked.status_code == 201
            forked_body = response_object(forked)
            assert forked_body["parent_session_id"] == session_id
            history = await client.get(f"/v1/sessions/{forked_body['id']}/history", headers=headers)
            assert history.status_code == 200
            history_events = EVENT_LIST.validate_python(cast(object, history.json()))
            inherited_answer = next(
                event for event in history_events if event["type"] == "assistant.message"
            )
            verified = await client.get(
                f"/v1/events/{inherited_answer['id']}/verify", headers=headers
            )
            assert verified.status_code == 200
            assert response_object(verified)["ok"] is True

            changed_mode = await client.put(
                f"/v1/sessions/{session_id}/mode",
                headers=headers,
                json={"mode": "plan"},
            )
            assert changed_mode.status_code == 200
            invalidated_usage = await client.get(
                f"/v1/sessions/{session_id}/usage", headers=headers
            )
            assert invalidated_usage.json()["latest_context"] is None
    finally:
        await state.runs.close()


@pytest.mark.parametrize("interaction_mode", ["auto", "plan"])
@pytest.mark.asyncio
async def test_structured_commentary_and_reasoning_keep_their_tool_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interaction_mode: str,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    provider = StructuredMessageProvider()
    state = GatewayState.create(paths, providers={provider.profile_id: provider})
    live_text = ""
    streamed_messages: list[str] = []
    publish = state.broker.publish

    async def capture(session_id: str, envelope: dict[str, object]) -> None:
        nonlocal live_text
        if envelope.get("type") == "response.text_delta":
            payload = JSON_OBJECT.validate_python(envelope["payload"])
            live_text += str(payload.get("text", ""))
        event = envelope.get("event")
        if (
            isinstance(event, dict)
            and JSON_OBJECT.validate_python(event).get("type") == "assistant.message"
        ):
            streamed_messages.append(live_text)
            live_text = ""
        await publish(session_id, envelope)

    monkeypatch.setattr(state.broker, "publish", capture)
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": provider.profile_id,
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(
                f"/v1/sessions/{session_id}/mode",
                headers=headers,
                json={"mode": interaction_mode},
            )
            assert (
                await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            ).status_code == 200
            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Inspect, then answer."},
            )
            run_id = str(response_object(accepted)["run_id"])
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            run_events = [event for event in events if event.get("run_id") == run_id]
            visible = [
                event
                for event in run_events
                if event["type"] in {"assistant.reasoning", "assistant.message", "model.tool_call"}
            ]
            assert [event["type"] for event in visible] == [
                "assistant.reasoning",
                "assistant.message",
                "model.tool_call",
                "assistant.reasoning",
                "assistant.message",
            ]
            messages = [
                JSON_OBJECT.validate_python(event["payload"])
                for event in visible
                if event["type"] == "assistant.message"
            ]
            assert [message["content"] for message in messages] == [
                "I'll inspect this first.",
                "Final answer only.",
            ]
            # Inspect the transient stream, not just the corrected durable answer.
            assert streamed_messages[-1] == "Final answer only."
            reasoning = [
                JSON_OBJECT.validate_python(event["payload"])
                for event in visible
                if event["type"] == "assistant.reasoning"
            ]
            assert [item["content"] for item in reasoning] == [
                "Inspect the workspace.",
                "The evidence is sufficient.",
            ]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_agent_question_pauses_and_resumes_the_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hames.runtime import ActiveClock

    def short_clock(_: float) -> ActiveClock:
        return ActiveClock(1)

    monkeypatch.setattr("hames.runtime.ActiveClock", short_clock)
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    provider = QuestionProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Ask me before choosing a visual direction."},
            )
            assert accepted.status_code == 202, accepted.text
            run_id = str(response_object(accepted)["run_id"])
            events = await _wait_for_event(client, headers, session_id, "question.requested")
            requested = next(event for event in events if event["type"] == "question.requested")
            assert requested["run_id"] == run_id
            await asyncio.sleep(1.2)
            assert state.runs.is_session_active(session_id)
            payload = JSON_OBJECT.validate_python(requested["payload"])
            assert payload["options"] == [
                {
                    "label": "Subdued",
                    "description": "Calm contrast.\n\nUse motion sparingly.",
                },
                {
                    "label": "High contrast",
                    "description": "Stronger separation between controls.",
                },
                {
                    "label": "Terminal native",
                    "description": "Favor the active terminal palette.",
                },
            ]
            assert not any(event["type"] == "run.completed" for event in events)

            resolved = await client.post(
                f"/v1/questions/{payload['question_id']}",
                headers=headers,
                json={"selected_option": "Subdued", "note": "Keep it calm"},
            )
            assert resolved.status_code == 200, resolved.text
            assert response_object(resolved) == {
                "question_id": payload["question_id"],
                "answer": "Subdued\nNote: Keep it calm",
                "answer_type": "single_choice",
                "selected_option": "Subdued",
                "selected_description": "Calm contrast.\n\nUse motion sparingly.",
                "selected_options": ["Subdued"],
                "selected_descriptions": ["Calm contrast.\n\nUse motion sparingly."],
                "note": "Keep it calm",
                "custom": False,
            }
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            types = [event["type"] for event in events]
            assert types.index("question.requested") < types.index("question.answered")
            assert types.index("question.answered") < types.index("tool.completed")
            assert types.index("tool.completed") < types.index("run.completed")
            answer = next(
                event for event in reversed(events) if event["type"] == "assistant.message"
            )
            assert answer["payload"]["content"] == "I will use the subdued direction."  # type: ignore[index]
            assert len(provider.requests) == 2
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_agent_supports_multiple_choice_and_text_questions(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    provider = QuestionKindsProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Ask me what to run and what to call the release."},
            )
            run_id = str(response_object(accepted)["run_id"])
            events = await _wait_for_event(client, headers, session_id, "question.requested")
            requested = next(event for event in events if event["type"] == "question.requested")
            payload = JSON_OBJECT.validate_python(requested["payload"])
            assert payload["answer_type"] == "multiple_choice"
            assert payload["min_selections"] == 2
            assert payload["max_selections"] == 3

            invalid = await client.post(
                f"/v1/questions/{payload['question_id']}",
                headers=headers,
                json={"selected_options": ["Unit"]},
            )
            assert invalid.status_code == 422
            resolved = await client.post(
                f"/v1/questions/{payload['question_id']}",
                headers=headers,
                json={"selected_options": ["Browser", "Unit"]},
            )
            assert resolved.status_code == 200, resolved.text
            resolution = response_object(resolved)
            assert resolution["selected_options"] == ["Unit", "Browser"]
            assert resolution["selected_descriptions"] == ["Fast checks.", "Rendered checks."]

            events = await _wait_for_event(
                client, headers, session_id, "question.requested", occurrences=2
            )
            questions = [event for event in events if event["type"] == "question.requested"]
            text_payload = JSON_OBJECT.validate_python(questions[-1]["payload"])
            assert text_payload["answer_type"] == "text"
            assert text_payload["options"] == []
            assert text_payload["placeholder"] == "Release name"

            text_resolution = await client.post(
                f"/v1/questions/{text_payload['question_id']}",
                headers=headers,
                json={"custom_answer": "Moonrise"},
            )
            assert text_resolution.status_code == 200, text_resolution.text
            assert response_object(text_resolution)["answer"] == "Moonrise"
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            completed = next(event for event in events if event["type"] == "run.completed")
            assert completed["run_id"] == run_id
            assert len(provider.requests) == 3
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_malformed_tool_arguments_retry_without_abandoning_the_run(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    fake = QwenFakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TEXT_DELTA,
                    text="Now let me write the file.",
                ),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="broken-write",
                        name="write_file",
                        arguments_delta='{"path":"recovered.txt","content":"ok"',
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TEXT_DELTA,
                    text="Now let me write the file.",
                ),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="valid-write",
                        name="write_file",
                        arguments_delta='{"path":"recovered.txt","content":"ok"}',
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Recovered and finished."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "qwen3.8-27b",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Write the file and keep going if a tool call is malformed."},
            )
            events = await _wait_for_event(client, headers, session_id, "run.completed")

            failure = next(
                event
                for event in events
                if event["type"] == "tool.failed" and event["payload"]["name"] == "write_file"  # type: ignore[index]
            )
            assert failure["payload"]["structured_data"] == {  # type: ignore[index]
                "code": "invalid_tool_arguments"
            }
            assert "smaller, complete scaffold or patch" in failure["payload"]["summary"]  # type: ignore[index]
            assert not any(event["type"] == "model.response.failed" for event in events)
            assert not any(event["type"] == "run.continuation.requested" for event in events)
            assert fake.requests[0].reasoning_budget_tokens is None
            assert fake.requests[1].reasoning_budget_tokens == 512
            assert any(
                message.role == "tool" and "invalid_tool_arguments" in message.content
                for message in fake.requests[1].messages
            )
            assert (tmp_path / "recovered.txt").read_text(encoding="utf-8") == "ok"
            assert any(
                event["type"] == "assistant.message"
                and event["payload"]["content"] == "Recovered and finished."  # type: ignore[index]
                for event in events
            )
            assert (
                sum(
                    event["type"] == "assistant.message"
                    and event["payload"]["content"] == "Now let me write the file."  # type: ignore[index]
                    for event in events
                )
                == 1
            )
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_task_cannot_be_blocked_by_an_unverified_provider_capability_claim(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    provider = FalseBlockerProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Create a file."},
            )
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            rejected = next(
                event
                for event in events
                if event["type"] == "tool.rejected" and event["payload"]["name"] == "task_update"  # type: ignore[index]
            )
            assert "cannot mark a task blocked" in rejected["payload"]["summary"]  # type: ignore[index]
            tasks = response_object(
                await client.get(f"/v1/sessions/{session_id}/tasks", headers=headers)
            )
            assert tasks["items"][0]["status"] == "pending"  # type: ignore[index,union-attr]
            assert "read-only" not in str(tasks["items"][0]["text"])  # type: ignore[index,union-attr]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_next_run_repairs_legacy_false_read_only_task_and_memory(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    provider = FakeProvider(
        [
            StreamEvent(kind=StreamEventKind.STARTED),
            StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Continuing."),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]
    )
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            session = state.ledger.get_session(session_id)
            tasks, _ = state.runs.session_tasks.add(
                session,
                text="Create the requested file",
            )
            task = tasks.items[0]
            state.runs.session_tasks.update(
                session,
                task.id,
                text=(
                    "Create the requested file (blocked: current session filesystem is read-only)."
                ),
                status="blocked",
            )
            source = state.ledger.append(
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="user.message",
                payload={"content": "poisoned provider result"},
            )
            memory = state.runs.memory.create_candidate(
                session=session,
                candidate=MemoryCandidate.model_validate(
                    {
                        "layer": "episodic",
                        "visibility": "workspace",
                        "subject": "session:test",
                        "predicate": "run_outcome",
                        "value": "false blocker",
                        "summary": (
                            "Actions: read .codex/memories/MEMORY.md. Outcome: the current "
                            "session has a read-only filesystem."
                        ),
                        "confidence": 1.0,
                        "importance": 0.8,
                        "provenance_event_ids": [source.id],
                        "evidence_basis": "assistant_inference",
                    }
                ),
                run_id="poisoned-run",
                origin_kind="automatic",
                activate=True,
                causation_id=source.id,
            ).record

            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Continue."},
            )
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            assert any(
                event["type"] == "runtime.notice"
                and event["payload"]["code"] == "provider_state_repaired"  # type: ignore[index]
                for event in events
            )
            repaired = await state.runs.current_tasks(session_id)
            assert repaired.items[0].status == "pending"
            assert repaired.items[0].text == "Create the requested file"
            assert state.runs.memory.get(memory.id).status == "retracted"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_explicit_heal_repairs_scars_without_changing_plan_mode(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    fake = FakeProvider([], turns=[])
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.put(
                f"/v1/sessions/{session_id}/mode", headers=headers, json={"mode": "plan"}
            )
            session = state.ledger.get_session(session_id)
            evidence = state.ledger.append(
                session_id=session_id,
                agent_id=session.agent_id,
                event_type="tool.started",
                payload={"tool_call_id": "heal-source", "name": "fixture"},
            )
            candidate = state.evolution.store.record_candidate(
                session=session,
                title="Use the corrected source",
                severity="medium",
                failure_signature="assistant:wrong_source",
                description="The assistant used the wrong source.",
                expected_behavior="Use the corrected source.",
                evidence_event_ids=[evidence.id],
            )
            scar = state.evolution.store.open(
                session=session,
                scar_id=candidate.scar.id,
                reason="explicit healing test",
            ).scar
            assert fake.turns is not None
            fake.turns.extend(
                [
                    [
                        StreamEvent(kind=StreamEventKind.STARTED),
                        StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call=ToolCallDelta(
                                index=0,
                                provider_call_id="heal-scar",
                                name="scar_control",
                                arguments_delta=json.dumps(
                                    {
                                        "scar_id": scar.id,
                                        "action": "repair",
                                        "reason": "explicit /heal maintenance",
                                    }
                                ),
                            ),
                        ),
                        StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
                    ],
                    [
                        StreamEvent(kind=StreamEventKind.STARTED),
                        StreamEvent(
                            kind=StreamEventKind.TEXT_DELTA,
                            text="The scar now has a durable guard.",
                        ),
                        StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
                    ],
                ]
            )

            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Heal behavioral scars now.", "purpose": "heal"},
            )
            assert accepted.status_code == 202, accepted.text
            await _wait_for_event(client, headers, session_id, "run.completed")

            assert state.ledger.get_session(session_id).title == "Heal scars"
            assert state.evolution.store.get(scar.id).status == "guarded"
            assert state.ledger.get_session(session_id).interaction_mode == "plan"
            assert "Execution mode is auto" in fake.requests[0].system
            assert "explicit scar-healing maintenance run" in fake.requests[0].system
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_dream_starts_only_after_a_solid_run_has_been_idle(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[runtime]\ndream_idle_seconds = 0.02\n"
        "[memory]\nenabled = false\n"
        "[skills]\nenabled = false\n"
        "[evolution]\nenabled = false\n",
        encoding="utf-8",
    )
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="write-before-dream",
                        name="write_file",
                        arguments_delta='{"path":"done.txt","content":"done"}',
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Finished."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Create the file."},
            )
            events = await _wait_for_event(client, headers, session_id, "dream.completed")
            types = [str(event["type"]) for event in events]
            assert types.index("run.completed") < types.index("dream.started")
            assert types.index("dream.started") < types.index("dream.completed")
            assert "memory.job.queued" not in types
            assert "skill.job.queued" not in types
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_plan_round_with_safe_tool_work_never_schedules_dream(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[runtime]\ndream_idle_seconds = 0.01\n"
        "[memory]\nenabled = false\n"
        "[skills]\nenabled = false\n"
        "[evolution]\nenabled = false\n",
        encoding="utf-8",
    )
    plan = f"# Safe plan\n\n## Tasks\n- [ ] Implement later\n\n{PLAN_READY_MARKER}"
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="plan-pwd",
                        name="shell",
                        arguments_delta='{"command":"cd /tmp && pwd"}',
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text=plan),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.put(
                f"/v1/sessions/{session_id}/mode", headers=headers, json={"mode": "plan"}
            )
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Inspect and make a plan."},
            )
            await _wait_for_event(client, headers, session_id, "run.completed")
            await asyncio.sleep(0.05)
            history = EVENT_LIST.validate_python(
                cast(
                    object,
                    (await client.get(f"/v1/sessions/{session_id}/events", headers=headers)).json(),
                )
            )
            assert "tool.completed" in [str(event["type"]) for event in history]
            assert not any(str(event["type"]).startswith("dream.") for event in history)
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_plan_review_execution_and_session_tasks_lifecycle(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    plan_markdown = "# Ship it\n\n## Tasks\n- [ ] Implement lifecycle\n- [ ] Verify behavior"
    fake = PlanExecutionProvider(plan_markdown)
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.put(
                f"/v1/sessions/{session_id}/mode", headers=headers, json={"mode": "plan"}
            )
            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Plan this change"},
            )
            assert accepted.status_code == 202
            events = await _wait_for_event(client, headers, session_id, "plan.proposed")
            assistant = next(event for event in events if event["type"] == "assistant.message")
            assert assistant["payload"]["content"] == plan_markdown  # type: ignore[index]
            assert PLAN_READY_MARKER not in str(assistant["payload"])  # type: ignore[index]
            assert "task_update" not in {tool.name for tool in fake.requests[0].tools}

            plan = response_object(
                await client.get(f"/v1/sessions/{session_id}/plans/current", headers=headers)
            )
            current = plan["current"]
            assert isinstance(current, dict)
            assert current["status"] == "ready"
            assert current["tasks"] == ["Implement lifecycle", "Verify behavior"]
            await _wait_for_event(client, headers, session_id, "run.completed")
            unapproved_tasks = response_object(
                await client.get(f"/v1/sessions/{session_id}/tasks", headers=headers)
            )
            assert unapproved_tasks["items"] == []

            executed = await client.post(
                f"/v1/sessions/{session_id}/plans/current/execute",
                headers=headers,
                json={
                    "strategy": "keep",
                    "note": "Keep the existing public API compatible",
                },
            )
            assert executed.status_code == 202
            execution = response_object(executed)
            seeded = execution["tasks"]
            assert isinstance(seeded, dict)
            assert [item["text"] for item in seeded["items"]] == [  # type: ignore[union-attr,index]
                "Implement lifecycle",
                "Verify behavior",
            ]
            await _wait_for_event(client, headers, session_id, "plan.execution.completed")
            session = response_object(
                await client.get(f"/v1/sessions/{session_id}", headers=headers)
            )
            assert session["interaction_mode"] == "auto"
            assert plan_markdown in fake.requests[1].system
            assert "Keep the existing public API compatible" in fake.requests[1].system
            assert (
                "Keep the existing public API compatible" in fake.requests[1].messages[-1].content
            )
            assert "Current session checklist" in fake.requests[1].system
            completed_plan = response_object(
                await client.get(f"/v1/sessions/{session_id}/plans/current", headers=headers)
            )
            assert completed_plan["current"]["status"] == "completed"  # type: ignore[index]
            assert completed_plan["current"]["execution_note"] == (  # type: ignore[index]
                "Keep the existing public API compatible"
            )

            added = await client.post(
                f"/v1/sessions/{session_id}/tasks",
                headers=headers,
                json={"text": "Discovered follow-up"},
            )
            assert added.status_code == 201
            added_body = response_object(added)
            added_items = added_body["items"]
            assert isinstance(added_items, list)
            task_id = str(added_items[-1]["id"])  # type: ignore[index]
            updated = await client.patch(
                f"/v1/sessions/{session_id}/tasks/{task_id}",
                headers=headers,
                json={"status": "completed", "position": 0},
            )
            assert response_object(updated)["items"][0]["status"] == "completed"  # type: ignore[index,union-attr]
            removed = await client.delete(
                f"/v1/sessions/{session_id}/tasks/{task_id}", headers=headers
            )
            assert all(
                item["id"] != task_id
                for item in response_object(removed)["items"]  # type: ignore[union-attr]
            )
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_switching_ready_plan_to_auto_approves_and_starts_execution(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    plan_markdown = (
        "# Continue automatically\n\n## Tasks\n- [ ] Implement change\n- [ ] Verify change"
    )
    provider = PlanExecutionProvider(plan_markdown)
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.put(
                f"/v1/sessions/{session_id}/mode", headers=headers, json={"mode": "plan"}
            )
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Plan this change"},
            )
            await _wait_for_event(client, headers, session_id, "run.completed")

            before = response_object(
                await client.get(f"/v1/sessions/{session_id}/tasks", headers=headers)
            )
            assert before["items"] == []

            changed = await client.put(
                f"/v1/sessions/{session_id}/mode",
                headers=headers,
                json={"mode": "auto"},
            )
            assert changed.status_code == 200
            assert response_object(changed)["interaction_mode"] == "auto"

            await _wait_for_event(client, headers, session_id, "plan.execution.completed")
            after = response_object(
                await client.get(f"/v1/sessions/{session_id}/tasks", headers=headers)
            )
            after_items = after["items"]
            assert isinstance(after_items, list)
            assert [item["text"] for item in after_items] == [  # type: ignore[index]
                "Implement change",
                "Verify change",
            ]
            assert len(provider.requests) >= 2
            assert plan_markdown in provider.requests[1].system
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_truncated_plan_execution_continues_with_reasoning_and_tasks(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    plan_markdown = "# Continue it\n\n## Tasks\n- [ ] Implement change\n- [ ] Verify change"
    fake = PlanExecutionProvider(plan_markdown, truncate_first_execution=True)
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.put(
                f"/v1/sessions/{session_id}/mode", headers=headers, json={"mode": "plan"}
            )
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Plan this change"},
            )
            await _wait_for_event(client, headers, session_id, "plan.proposed")
            executed = await client.post(
                f"/v1/sessions/{session_id}/plans/current/execute",
                headers=headers,
                json={"strategy": "keep", "note": "Preserve continuity"},
            )
            assert executed.status_code == 202
            events = await _wait_for_event(client, headers, session_id, "plan.execution.completed")
            execution_run_id = str(response_object(executed)["run_id"])
            run_events = [event for event in events if event.get("run_id") == execution_run_id]
            event_types = [str(event["type"]) for event in run_events]
            assert "run.continuation.requested" in event_types
            assert event_types.index("run.continuation.requested") < event_types.index(
                "run.completed"
            )
            first_reasoning = next(
                event for event in run_events if event["type"] == "assistant.reasoning"
            )
            assert first_reasoning["payload"]["status"] == "interrupted"  # type: ignore[index]
            continuation_request = fake.requests[2]
            assert "reached its output limit" in continuation_request.system
            assert any(
                message.reasoning_content
                == "I will begin with the first implementation task, then verify it."
                for message in continuation_request.messages
            )
            tasks = response_object(
                await client.get(f"/v1/sessions/{session_id}/tasks", headers=headers)
            )
            assert all(item["status"] == "completed" for item in tasks["items"])  # type: ignore[union-attr,index]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_plan_execution_continues_until_the_configured_model_turn_limit(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[memory]\nenabled = false\n[runtime]\nmax_model_turns_per_user_message = 4\n",
        encoding="utf-8",
    )
    provider = StalledPlanExecutionProvider("# Stalled\n\n## Tasks\n- [ ] Do the work")
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.put(
                f"/v1/sessions/{session_id}/mode", headers=headers, json={"mode": "plan"}
            )
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Plan it"},
            )
            await _wait_for_event(client, headers, session_id, "plan.proposed")
            executed = await client.post(
                f"/v1/sessions/{session_id}/plans/current/execute",
                headers=headers,
                json={"strategy": "keep"},
            )
            events = await _wait_for_event(client, headers, session_id, "plan.execution.attention")
            run_id = str(response_object(executed)["run_id"])
            run_events = [event for event in events if event.get("run_id") == run_id]
            failure = next(event for event in run_events if event["type"] == "run.failed")
            assert failure["payload"]["code"] == "model_turn_limit"  # type: ignore[index]
            assert sum(event["type"] == "run.continuation.requested" for event in run_events) == 4
            assert not any(
                event["type"] == "run.failed" and event["payload"]["code"] == "run_stalled"  # type: ignore[index]
                for event in run_events
            )
            assert not any(event["type"] == "run.completed" for event in run_events)
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_planning_only_run_still_runs_wrap_up_observers(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    provider = PlanExecutionProvider("# Action plan\n\n## Tasks\n- [ ] Do the work")
    observer = BlockingPostRunObserver()
    state = GatewayState.create(paths, providers={"fake": provider})
    state.runs.evolution_manager = cast(object, observer)  # type: ignore[assignment]
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.put(
                f"/v1/sessions/{session_id}/mode", headers=headers, json={"mode": "plan"}
            )
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Plan the work"},
            )
            await _wait_for_event(client, headers, session_id, "run.completed")
            await asyncio.wait_for(observer.started.wait(), timeout=1)
    finally:
        observer.release.set()
        await state.runs.close()


@pytest.mark.asyncio
async def test_plan_notes_wait_for_draft_then_coalesce_in_order(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = PlanRevisionProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.put(
                f"/v1/sessions/{session_id}/mode", headers=headers, json={"mode": "plan"}
            )
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Draft a plan"},
            )
            await provider.first_started.wait()
            for note in ("Prefer the smaller API", "Add a migration test"):
                queued = await client.post(
                    f"/v1/sessions/{session_id}/plans/current/notes",
                    headers=headers,
                    json={"content": note},
                )
                assert response_object(queued)["disposition"] == "queued"
            provider.release_first.set()
            events = await _wait_for_event(
                client, headers, session_id, "plan.proposed", occurrences=2
            )
            assert len(provider.requests) == 2
            revision_prompt = provider.requests[1].messages[-1].content
            assert revision_prompt.index("Prefer the smaller API") < revision_prompt.index(
                "Add a migration test"
            )
            applied = [event for event in events if event["type"] == "plan.note.applied"]
            assert applied[-1]["payload"]["contents"] == [  # type: ignore[index]
                "Prefer the smaller API",
                "Add a migration test",
            ]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_new_foreground_run_can_start_during_post_terminal_observation(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = ForegroundOverlapProvider()
    observer = BlockingPostRunObserver()
    state = GatewayState.create(paths, providers={"fake": provider})
    state.runs.evolution_manager = cast(object, observer)  # type: ignore[assignment]
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)

            first = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "first"},
            )
            assert first.status_code == 202
            await _wait_for_event(client, headers, session_id, "run.completed")
            await asyncio.wait_for(observer.started.wait(), timeout=1)
            assert state.runs.active_run_count == 0
            assert not state.runs.is_session_active(session_id)

            second = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "second"},
            )
            assert second.status_code == 202
            await asyncio.wait_for(provider.second_started.wait(), timeout=1)

            observer.release.set()
            await asyncio.sleep(0)
            assert state.runs.is_session_active(session_id)

            provider.release_second.set()
            await _wait_for_event(client, headers, session_id, "run.completed", occurrences=2)
    finally:
        observer.release.set()
        provider.release_second.set()
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_closes_session_without_erasing_audit_history(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            assert created.status_code == 201
            session_id = str(response_object(created)["id"])

            pinned = await client.put(
                f"/v1/sessions/{session_id}/pinned",
                headers=headers,
                json={"pinned": True},
            )
            assert pinned.status_code == 200
            assert response_object(pinned)["pinned"] is True

            closed = await client.delete(f"/v1/sessions/{session_id}", headers=headers)
            assert closed.status_code == 200
            assert response_object(closed)["status"] == "closed"

            recent = await client.get(
                "/v1/sessions/recent",
                headers=headers,
                params={"working_directory": str(tmp_path)},
            )
            assert recent.status_code == 200
            assert recent.json() is None

            history = await client.get(f"/v1/sessions/{session_id}/history", headers=headers)
            assert history.status_code == 200
            events = EVENT_LIST.validate_python(cast(object, history.json()))
            assert [event["type"] for event in events] == [
                "session.opened",
                "session.pinned.changed",
                "session.closed",
            ]

            closed_again = await client.delete(f"/v1/sessions/{session_id}", headers=headers)
            assert closed_again.status_code == 409
            error = response_object(closed_again)["error"]
            assert isinstance(error, dict)
            assert error["code"] == "session_not_open"
            missing = await client.delete("/v1/sessions/missing", headers=headers)
            assert missing.status_code == 404
    finally:
        await state.runs.close()


class TwoModelProvider(FakeProvider):
    async def list_models(self) -> list[ProviderModel]:
        models = await super().list_models()
        return [models[0], models[0].model_copy(update={"id": "other"})]


class CountingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__([])
        self.probes = 0

    async def list_models(self) -> list[ProviderModel]:
        self.probes += 1
        return await super().list_models()


@pytest.mark.asyncio
async def test_fresh_session_uses_config_defaults_while_inherited_session_keeps_settings(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    reviewer = paths.agents / "reviewer"
    reviewer.mkdir()
    (reviewer / "AGENT.md").write_text(
        '---\nid: reviewer\nname: Reviewer\nprovider: inherit\nmodel: ""\n---\nReview carefully.\n',
        encoding="utf-8",
    )
    paths.config_file.write_text(
        """\
[runtime]
default_agent = "reviewer"
default_provider = "fake"
default_model = "fixture"
default_reasoning_effort = "medium"
default_interaction_mode = "auto"

[providers.fake]
adapter = "llama_cpp"
base_url = "http://127.0.0.1:8080"
""",
        encoding="utf-8",
    )
    state = GatewayState.create(paths, providers={"fake": TwoModelProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            fresh = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path)},
            )
            assert fresh.status_code == 201, fresh.text
            fresh_body = response_object(fresh)
            assert fresh_body["agent_id"] == "reviewer"
            assert fresh_body["provider"] == "fake"
            assert fresh_body["model"] == "fixture"
            assert fresh_body["reasoning_effort"] == "medium"
            assert fresh_body["interaction_mode"] == "auto"
            session_id = str(fresh_body["id"])

            changed_model = await client.patch(
                f"/v1/sessions/{session_id}",
                headers=headers,
                json={"provider": "fake", "model": "other", "reasoning_effort": "xhigh"},
            )
            assert changed_model.status_code == 200
            changed_agent = await client.put(
                f"/v1/sessions/{session_id}/agent",
                headers=headers,
                json={"agent_id": "default"},
            )
            assert changed_agent.status_code == 200
            changed_mode = await client.put(
                f"/v1/sessions/{session_id}/mode",
                headers=headers,
                json={"mode": "plan"},
            )
            assert changed_mode.status_code == 200

            contextual = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path)},
            )
            assert contextual.status_code == 201, contextual.text
            contextual_body = response_object(contextual)
            assert contextual_body["agent_id"] == "default"
            assert contextual_body["provider"] == "fake"
            assert contextual_body["model"] == "fixture"
            assert contextual_body["reasoning_effort"] == "medium"
            assert contextual_body["interaction_mode"] == "auto"

            inherited = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "inherit_session_id": session_id,
                },
            )
            assert inherited.status_code == 201, inherited.text
            inherited_body = response_object(inherited)
            assert inherited_body["agent_id"] == "default"
            assert inherited_body["provider"] == "fake"
            assert inherited_body["model"] == "other"
            assert inherited_body["reasoning_effort"] == "xhigh"
            assert inherited_body["interaction_mode"] == "plan"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_provider_listing_is_offline_and_probe_is_explicit(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = CountingProvider()
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/v1/providers", headers=headers)
            assert listed.status_code == 200
            assert fake.probes == 0
            profiles = listed.json()
            assert profiles[0]["id"] == "fake"
            assert profiles[0]["adapter"] == "fake"

            probed = await client.post("/v1/providers/fake/probe", headers=headers)
            assert probed.status_code == 200
            assert probed.json()["reachable"] is True
            assert fake.probes == 1

            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            assert created.status_code == 201
            source_id = str(response_object(created)["id"])
            await client.put(
                f"/v1/sessions/{source_id}/mode",
                headers=headers,
                json={"mode": "plan"},
            )
            probes_before_clone = fake.probes
            cloned = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "inherit_session_id": source_id,
                },
            )
            assert cloned.status_code == 201
            cloned_body = response_object(cloned)
            assert cloned_body["model"] == "fixture"
            assert cloned_body["interaction_mode"] == "plan"
            assert fake.probes == probes_before_clone

            conflict = await client.get(
                "/v1/events",
                headers={**headers, "Last-Event-ID": "5"},
                params={"session_id": "fixture", "after_sequence": 4},
            )
            assert conflict.status_code == 400
            conflict_error = response_object(conflict)["error"]
            assert isinstance(conflict_error, dict)
            assert conflict_error["code"] == "conflicting_event_cursor"

            invalid = await client.get(
                "/v1/events",
                headers={**headers, "Last-Event-ID": "event-id"},
                params={"session_id": "fixture"},
            )
            assert invalid.status_code == 400
            invalid_error = response_object(invalid)["error"]
            assert isinstance(invalid_error, dict)
            assert invalid_error["code"] == "invalid_event_cursor"
    finally:
        await state.runs.close()


class StallingProvider(FakeProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        yield StreamEvent(kind=StreamEventKind.REASONING_DELTA, text="partial")
        await asyncio.Event().wait()


class GatedToolProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__([])
        self.started = asyncio.Event()
        self.release_tool = asyncio.Event()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        self.started.set()
        await self.release_tool.wait()
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call=ToolCallDelta(
                index=0,
                provider_call_id="mid-flight-write",
                name="write_file",
                arguments_delta='{"path":"mid-flight.txt","content":"blocked"}',
            ),
        )
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")


@pytest.mark.asyncio
async def test_mode_change_applies_at_next_tool_boundary_mid_flight(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = GatedToolProvider()
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Prepare a write."},
            )
            await asyncio.wait_for(fake.started.wait(), timeout=1)
            changed = await client.put(
                f"/v1/sessions/{session_id}/mode",
                headers=headers,
                json={"mode": "manual"},
            )
            assert response_object(changed)["interaction_mode"] == "manual"
            fake.release_tool.set()

            events = await _wait_for_event(client, headers, session_id, "approval.requested")
            requested = next(event for event in events if event["type"] == "approval.requested")
            payload = requested["payload"]
            assert isinstance(payload, dict)
            assert payload["allow_session"] is True
            await client.post(
                f"/v1/approvals/{payload['approval_id']}",
                headers=headers,
                json={
                    "request_hash": payload["request_hash"],
                    "decision": "denied",
                },
            )
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_requires_model_choice_for_multiple_models(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": TwoModelProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake"},
            )
            assert response.status_code == 409
            error = response_object(response)["error"]
            assert isinstance(error, dict)
            assert error["code"] == "model_selection_required"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_explicit_cancellation_persists_partial_reasoning(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": StallingProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Wait"},
            )
            run_id = str(response_object(accepted)["run_id"])
            events: list[dict[str, JsonValue]] = []
            for _ in range(100):
                events_response = await client.get(
                    f"/v1/sessions/{session_id}/events", headers=headers
                )
                events = EVENT_LIST.validate_python(cast(object, events_response.json()))
                if any(event["type"] == "model.response.started" for event in events):
                    break
                await asyncio.sleep(0.01)

            cancelled = await client.post(f"/v1/runs/{run_id}/cancel", headers=headers)
            assert cancelled.status_code == 200
            for _ in range(100):
                events_response = await client.get(
                    f"/v1/sessions/{session_id}/events", headers=headers
                )
                events = EVENT_LIST.validate_python(cast(object, events_response.json()))
                if any(event["type"] == "run.cancelled" for event in events):
                    break
                await asyncio.sleep(0.01)
            reasoning = next(event for event in events if event["type"] == "assistant.reasoning")
            payload = reasoning["payload"]
            assert isinstance(payload, dict)
            assert payload["content"] == "partial"
            assert payload["status"] == "interrupted"
            duration = payload["duration_seconds"]
            assert isinstance(duration, (int, float))
            assert duration >= 0
            assert sum(event["type"] == "run.cancelled" for event in events) == 1
            for _ in range(100):
                if state.runs.active_run_count == 0:
                    break
                await asyncio.sleep(0.01)
            assert state.runs.active_run_count == 0

            restarted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "The session lock was released"},
            )
            assert restarted.status_code == 202
            second_run_id = str(response_object(restarted)["run_id"])
            assert (
                await client.post(f"/v1/runs/{second_run_id}/cancel", headers=headers)
            ).status_code == 200
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_rejects_invalid_agent_before_creating_session(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    paths.default_agent.write_text("invalid capsule", encoding="utf-8")
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            assert created.status_code == 400
            error = response_object(created)["error"]
            assert isinstance(error, dict)
            assert error["code"] == "unknown_agent"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_lists_available_agent_tools(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/tools", headers=headers)

        assert response.status_code == 200
        assert response.json() == sorted(state.runs.tools.names())
        assert "read_file" in response.json()
        assert "memory_forget" in response.json()
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_creates_and_retires_custom_agent_but_protects_default(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    source = """---
{
  "id": "careful-reviewer",
  "name": "Careful Reviewer",
  "authority": "standard",
  "tools": {"allow": ["read_file"], "deny": ["write_file"]},
  "skills": {"allow": [], "deny": []}
}
---
# Role
Review changes carefully.
"""
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/agents",
                headers=headers,
                json={"authority": "standard", "source": source},
            )
            protected = await client.delete("/v1/agents/default", headers=headers)
            retired = await client.delete("/v1/agents/careful-reviewer", headers=headers)
            listed = await client.get("/v1/agents", headers=headers)

        assert created.status_code == 201
        assert created.json()["id"] == "careful-reviewer"
        assert created.json()["tools_deny"] == ["write_file"]
        assert protected.status_code == 409
        assert retired.status_code == 200
        assert [agent["id"] for agent in listed.json()] == ["default"]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_customizes_default_agent_without_making_it_deletable(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            updated = await client.patch(
                "/v1/agents/default",
                headers=headers,
                json={
                    "name": "Navigator",
                    "instructions": "# Role\nGuide the work carefully.",
                    "tools": {"allow": [], "deny": ["shell"]},
                    "skills": {
                        "allow": [],
                        "deny": ["deployment"],
                        "pin": ["testing"],
                    },
                },
            )
            listed = await client.get("/v1/agents", headers=headers)
            protected = await client.delete("/v1/agents/default", headers=headers)

        assert updated.status_code == 200
        assert updated.json()["id"] == "default"
        assert updated.json()["name"] == "Navigator"
        assert updated.json()["instructions"] == "# Role\nGuide the work carefully."
        assert updated.json()["tools_deny"] == ["shell"]
        assert updated.json()["skills_deny"] == ["deployment"]
        assert updated.json()["skills_pin"] == ["testing"]
        assert "name: Navigator" in updated.json()["source"]
        assert listed.json()[0]["name"] == "Navigator"
        assert protected.status_code == 409
        assert paths.default_agent.is_file()
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_persists_agent_avatar_in_list_and_detail(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    avatar = {"shape": "triangle", "eyes": "visor", "face": "solid", "color": "#0EA5E9"}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            updated = await client.patch(
                "/v1/agents/default", headers=headers, json={"avatar": avatar}
            )
            listed = await client.get("/v1/agents", headers=headers)
            detailed = await client.get("/v1/agents/default", headers=headers)

        expected = {**avatar, "color": "#0ea5e9"}
        assert updated.status_code == 200
        assert updated.json()["avatar"] == expected
        assert listed.json()[0]["avatar"] == expected
        assert detailed.json()["avatar"] == expected
        assert "avatar:" in detailed.json()["source"]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_lists_agent_editor_capabilities(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/agents/default/capabilities",
                headers=headers,
                params={"working_directory": str(tmp_path)},
            )

        assert response.status_code == 200
        assert "read_file" in response.json()["tools"]
        assert all("slug" in skill for skill in response.json()["skills"])
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_runtime_rejects_malformed_provider_order(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [
            StreamEvent(kind=StreamEventKind.STARTED),
            StreamEvent(kind=StreamEventKind.STARTED),
        ]
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Trigger malformed order"},
            )
            events = await _wait_for_event(client, headers, session_id, "run.failed")
            failure = next(event for event in events if event["type"] == "run.failed")["payload"]
            assert isinstance(failure, dict)
            assert failure["code"] == "provider_protocol_error"
            assert "model.response.completed" not in [event["type"] for event in events]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_runtime_executes_tool_and_continues_model_loop(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    (tmp_path / "README.md").write_text("fixture readme", encoding="utf-8")
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="call-1",
                        name="read_file",
                        arguments_delta='{"path":"README.md"}',
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="I read the fixture."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Inspect README"},
            )
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            call = next(event for event in events if event["type"] == "model.tool_call")
            payload = call["payload"]
            assert isinstance(payload, dict)
            assert payload["name"] == "read_file"
            assert payload["arguments"] == {"path": "README.md"}
            result = next(event for event in events if event["type"] == "tool.completed")
            result_payload = result["payload"]
            assert isinstance(result_payload, dict)
            assert result_payload["content"] == "fixture readme"
            assert len(fake.requests) == 2
            assert [message.role for message in fake.requests[1].messages][-2:] == [
                "assistant",
                "tool",
            ]
            assert fake.requests[1].messages[-1].tool_name == "read_file"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_goal_runs_multiple_bounded_steps_until_evidence_backed_achievement(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="goal-progress",
                        name="goal_report",
                        arguments_delta=json.dumps(
                            {
                                "status": "progress",
                                "summary": "Implemented the first half",
                                "evidence": ["first-half tests pass"],
                            }
                        ),
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Continuing next step."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="goal-achieved",
                        name="goal_report",
                        arguments_delta=json.dumps(
                            {
                                "status": "achieved",
                                "summary": "Feature is complete",
                                "evidence": ["full test suite passes"],
                            }
                        ),
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Goal achieved."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)

            started = await client.post(
                f"/v1/sessions/{session_id}/goals",
                headers=headers,
                json={"objective": "Complete the whole feature"},
            )
            assert started.status_code == 202
            assert response_object(started)["status"] == "running"
            assert state.ledger.get_session(session_id).title == "Complete the whole feature"
            events = await _wait_for_event(client, headers, session_id, "goal.achieved")

            history = await client.get(f"/v1/sessions/{session_id}/goals", headers=headers)
            assert history.status_code == 200
            goal = history.json()[0]
            assert goal["status"] == "achieved"
            assert goal["step_count"] == 2
            assert goal["latest_evidence"] == ["full test suite passes"]
            assert (
                await client.get(f"/v1/sessions/{session_id}/goals/current", headers=headers)
            ).json() is None
            assert sum(event["type"] == "goal.step.started" for event in events) == 2
            await _wait_for_event(client, headers, session_id, "run.completed", occurrences=2)
            assert len(fake.requests) == 4
            assert "Active autonomous goal" in fake.requests[0].system
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_goal_stall_guard_blocks_three_equivalent_unreported_steps(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    repeated = [
        StreamEvent(kind=StreamEventKind.STARTED),
        StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="I could not make progress."),
        StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
    ]
    fake = FakeProvider([], turns=[repeated, repeated, repeated])
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/goals",
                headers=headers,
                json={"objective": "Solve the stubborn problem"},
            )

            events = await _wait_for_event(client, headers, session_id, "goal.blocked")
            current = response_object(
                await client.get(f"/v1/sessions/{session_id}/goals/current", headers=headers)
            )
            assert current["status"] == "blocked"
            assert current["repeated_no_progress"] == 3
            assert sum(event["type"] == "goal.step.started" for event in events) == 3
            assert (
                sum(
                    any(tool.name == "goal_report" for tool in request.tools)
                    for request in fake.requests
                )
                == 3
            )
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_foreground_request_yields_goal_then_goal_resumes_after_queue_settles(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    provider = GoalForegroundProvider()
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/goals",
                headers=headers,
                json={"objective": "Complete after handling foreground work"},
            )
            await asyncio.wait_for(provider.goal_started.wait(), timeout=1)

            foreground = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Answer this first"},
            )
            assert response_object(foreground)["disposition"] == "queued"
            events = await _wait_for_event(client, headers, session_id, "goal.achieved")

            event_types = [event["type"] for event in events]
            yielded_index = event_types.index("goal.yielded")
            foreground_index = next(
                index
                for index, event in enumerate(events)
                if event["type"] == "user.message"
                and JSON_OBJECT.validate_python(event["payload"])["content"] == "Answer this first"
            )
            resumed_index = event_types.index("goal.resumed")
            assert yielded_index < foreground_index < resumed_index
            assert sum(event_type == "goal.step.started" for event_type in event_types) == 2
            await _wait_for_event(client, headers, session_id, "run.completed", occurrences=2)
            assert len(provider.requests) >= 4
            assert provider.requests[1].messages[-1].content == "Answer this first"
            assert "Active autonomous goal" in provider.requests[2].system
            assert "goal_report" not in {tool.name for tool in provider.requests[1].tools}
            assert "goal_report" in {tool.name for tool in provider.requests[2].tools}
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_manual_compaction_uses_active_provider_and_records_a_durable_cutoff(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [
            StreamEvent(kind=StreamEventKind.STARTED, provider_request_id="compact-request"),
            StreamEvent(kind=StreamEventKind.REASONING_DELTA, text="planning the summary"),
            StreamEvent(
                kind=StreamEventKind.TEXT_DELTA,
                text="Keep the earlier requirements and completed verification.",
            ),
            StreamEvent(kind=StreamEventKind.USAGE, usage=Usage(input_tokens=40, output_tokens=9)),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            for index in range(6):
                state.ledger.append(
                    session_id=session_id,
                    event_type="user.message",
                    payload={"content": f"turn {index}"},
                )

            accepted = await client.post(f"/v1/sessions/{session_id}/compact", headers=headers)
            assert accepted.status_code == 202
            run_id = str(response_object(accepted)["run_id"])
            events = await _wait_for_event(
                client, headers, session_id, "context.compaction.completed"
            )
            completed = next(
                event for event in events if event["type"] == "context.compaction.completed"
            )
            payload = JSON_OBJECT.validate_python(completed["payload"])
            assert completed["run_id"] == run_id
            assert state.ledger.get_session(session_id).title == "Compact conversation"
            assert payload["summary"] == "Keep the earlier requirements and completed verification."
            assert payload["turns_compacted"] == 2
            assert payload["provider"] == "fake"
            assert fake.requests[0].metadata == {"purpose": "context_compaction"}
            assert fake.requests[0].tools == []
            assert fake.requests[0].reasoning_effort == "off"
            assert fake.requests[0].reasoning_budget_tokens == 0
            requested = next(
                event
                for event in events
                if event["type"] == "model.requested" and event["run_id"] == run_id
            )
            requested_payload = JSON_OBJECT.validate_python(requested["payload"])
            assert requested_payload["purpose"] == "context_compaction"
            assert any(event["type"] == "model.usage" for event in events)
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_automatic_codex_compaction_preserves_only_the_latest_turn(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")

    def response(text: str) -> list[StreamEvent]:
        return [
            StreamEvent(kind=StreamEventKind.STARTED),
            StreamEvent(kind=StreamEventKind.TEXT_DELTA, text=text),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]

    fake = FakeProvider(
        [],
        turns=[
            response("rolling summary one"),
            response("done"),
        ],
    )
    fake.adapter = "codex"
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            for index in range(6):
                state.ledger.append(
                    session_id=session_id,
                    event_type="user.message",
                    payload={"content": f"old-{index}-" + ("x" * 170_000)},
                )

            sent = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "continue"},
            )
            assert sent.status_code == 202
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            compacted_events = [
                event for event in events if event["type"] == "context.compaction.completed"
            ]
            assert compacted_events, [
                (event["type"], event.get("payload"))
                for event in events
                if isinstance(event["type"], str)
                and ("compaction" in event["type"] or event["type"].startswith("run."))
            ]
            compacted = compacted_events[0]
            payload = JSON_OBJECT.validate_python(compacted["payload"])
            assert payload["trigger"] == "automatic"
            assert payload["preserve_recent_turns"] == 1
            assert payload["turns_compacted"] == 6
            assert payload["passes"] == 1
            assert len(fake.requests) == 2
            assert fake.requests[0].metadata == {"purpose": "context_compaction"}
            assert fake.requests[1].metadata == {
                "purpose": "agent",
                "workspace_path": str(tmp_path),
                "interaction_mode": "auto",
            }
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_automatic_compaction_honors_an_absolute_token_threshold(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "\n".join(
            [
                "[context]",
                "fallback_window_tokens = 131072",
                "output_reserve_tokens = 16384",
                "compaction_auto_threshold_ratio = 0.95",
                "compaction_auto_threshold_tokens = 4096",
                "compaction_preserve_recent_turns = 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    def response(text: str) -> list[StreamEvent]:
        return [
            StreamEvent(kind=StreamEventKind.STARTED),
            StreamEvent(kind=StreamEventKind.TEXT_DELTA, text=text),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]

    fake = FakeProvider(
        [],
        turns=[response("rolling summary"), response("done")],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            for index in range(3):
                state.ledger.append(
                    session_id=session_id,
                    event_type="user.message",
                    payload={"content": f"old-{index}-" + ("x" * 8_000)},
                )

            sent = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "continue"},
            )
            assert sent.status_code == 202
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            compacted = next(
                event for event in events if event["type"] == "context.compaction.completed"
            )
            payload = JSON_OBJECT.validate_python(compacted["payload"])
            assert payload["trigger"] == "automatic"
            assert fake.requests[0].metadata == {"purpose": "context_compaction"}
            assert fake.requests[0].reasoning_effort == "off"
            assert fake.requests[-1].metadata == {
                "purpose": "agent",
                "workspace_path": str(tmp_path),
                "interaction_mode": "auto",
            }
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_model_can_title_the_active_session(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="title-1",
                        name="session_title_set",
                        arguments_delta='{"title":"Theme and composer polish"}',
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="The session is titled."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Polish the current interface."},
            )
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            title_event = next(
                event for event in reversed(events) if event["type"] == "session.title.changed"
            )
            assert title_event["payload"] == {"title": "Theme and composer polish"}
            assert title_event["run_id"] is not None
            assert state.ledger.get_session(session_id).title == "Theme and composer polish"
            completed = next(
                event
                for event in events
                if event["type"] == "tool.completed"
                and isinstance(event["payload"], dict)
                and event["payload"].get("name") == "session_title_set"
            )
            assert completed["payload"]["structured_data"] == {  # type: ignore[index]
                "title": "Theme and composer polish"
            }
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_runtime_manages_memory_from_the_chat_tool_loop(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="memory-1",
                        name="memory_add",
                        arguments_delta=(
                            '{"layer":"relationship","visibility":"global",'
                            '"subject":"user:local","predicate":"prefers_response_style",'
                            '"value":"concise","summary":"The user prefers concise responses."}'
                        ),
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="I will remember that."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Remember that I prefer concise responses."},
            )
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            completed = next(
                event
                for event in events
                if event["type"] == "tool.completed"
                and isinstance(event["payload"], dict)
                and event["payload"].get("name") == "memory_add"
            )
            completed_payload = completed["payload"]
            assert isinstance(completed_payload, dict)
            assert str(completed_payload["summary"]).startswith("remembered")
            session = state.ledger.get_session(session_id)
            memories = state.runs.memory.list_visible(
                session, layer="relationship", query="concise responses"
            )
            assert len(memories) == 1
            assert memories[0].predicate == "prefers_response_style"
            assert memories[0].status == "active"
            assert "memory_search" in {tool.name for tool in fake.requests[0].tools}
            assert "scar_record" in {tool.name for tool in fake.requests[0].tools}
            assert "skill_control" in {tool.name for tool in fake.requests[0].tools}
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_manual_mode_offers_a_durable_session_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hames.runtime import ActiveClock

    # Human response time must exceed the active budget without expiring the run.
    def short_clock(_: float) -> ActiveClock:
        return ActiveClock(1)

    monkeypatch.setattr("hames.runtime.ActiveClock", short_clock)
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="manual-write",
                        name="write_file",
                        arguments_delta='{"path":"manual.txt","content":"approved"}',
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="The approved write completed."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            changed = await client.put(
                f"/v1/sessions/{session_id}/mode",
                headers=headers,
                json={"mode": "manual"},
            )
            assert response_object(changed)["interaction_mode"] == "manual"
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Write the approved fixture."},
            )
            events = await _wait_for_event(client, headers, session_id, "approval.requested")
            requested = next(event for event in events if event["type"] == "approval.requested")
            payload = requested["payload"]
            assert isinstance(payload, dict)
            assert payload["allow_session"] is True
            await asyncio.sleep(1.2)
            assert state.runs.is_session_active(session_id)
            resolved = await client.post(
                f"/v1/approvals/{payload['approval_id']}",
                headers=headers,
                json={
                    "request_hash": payload["request_hash"],
                    "decision": "approved_session",
                },
            )
            resolved_body = response_object(resolved)
            assert resolved_body["status"] == "approved"
            assert resolved_body["approval_scope"] == "session"
            await _wait_for_event(client, headers, session_id, "run.completed")
            assert (tmp_path / "manual.txt").read_text(encoding="utf-8") == "approved"
            assert state.controls.has_session_tool_grant(session_id, "write_file")
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_plan_mode_rejects_writes_in_the_gateway(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="plan-write",
                        name="write_file",
                        arguments_delta='{"path":"forbidden.txt","content":"no"}',
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="I stayed in plan mode."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.put(
                f"/v1/sessions/{session_id}/mode",
                headers=headers,
                json={"mode": "plan"},
            )
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Plan this change without writing."},
            )
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            rejected = next(event for event in events if event["type"] == "tool.rejected")
            payload = rejected["payload"]
            assert isinstance(payload, dict)
            assert payload["name"] == "write_file"
            assert "plan mode" in str(payload["summary"])
            assert not (tmp_path / "forbidden.txt").exists()
            assert "approval.requested" not in [event["type"] for event in events]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_runtime_delegates_with_an_explicit_task_card(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="delegate-1",
                        name="spawn_agent",
                        arguments_delta=(
                            '{"agent_id":"reviewer","task":"Review the request",'
                            '"evidence_event_ids":[]}'
                        ),
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Child review complete."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Parent answer."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    parent_capsule = paths.agents / "default" / "AGENT.md"
    parent_capsule.write_text(
        "---\n"
        "id: default\n"
        "name: Default\n"
        "delegation:\n"
        "  allow: true\n"
        "  allowed_agents: [reviewer]\n"
        "---\n"
        "Delegate only focused review tasks.\n",
        encoding="utf-8",
    )
    state.agents.create("Reviewer", authority="read_only")
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            parent_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{parent_id}/trust", headers=headers)
            accepted = await client.post(
                f"/v1/sessions/{parent_id}/messages",
                headers=headers,
                json={"content": "Please have a reviewer check this."},
            )
            run_id = str(response_object(accepted)["run_id"])
            events = await _wait_for_event(client, headers, parent_id, "run.completed")
            delegated = next(event for event in events if event["type"] == "delegation.completed")
            delegation = delegated["payload"]
            assert isinstance(delegation, dict)
            child_id = str(delegation["child_session_id"])
            child = response_object(await client.get(f"/v1/sessions/{child_id}", headers=headers))
            assert child["lineage_kind"] == "delegation"
            assert child["delegation_depth"] == 1
            child_events = EVENT_LIST.validate_python(
                cast(
                    object,
                    (await client.get(f"/v1/sessions/{child_id}/events", headers=headers)).json(),
                )
            )
            assert "delegation.task_card" in [event["type"] for event in child_events]
            child_history = EVENT_LIST.validate_python(
                cast(
                    object,
                    (await client.get(f"/v1/sessions/{child_id}/history", headers=headers)).json(),
                )
            )
            assert [event["type"] for event in child_history][:2] == [
                "session.opened",
                "delegation.task_card",
            ]
            result = next(event for event in events if event["type"] == "tool.completed")
            assert result["payload"]["name"] == "spawn_agent"  # type: ignore[index]
            assert len(fake.requests) == 3
            assert [tool.name for tool in fake.requests[1].tools] == [
                "ask_user",
                "read_file",
                "list_dir",
                "vcs_inspect",
                "agent_control",
                "skill_load",
                "memory_search",
                "scar_list",
                "skill_catalog",
                "session_title_set",
                "task_list",
                "task_update",
                "mcp_resource_list",
                "mcp_resource_read",
            ]
            assert fake.requests[2].messages[-1].tool_name == "spawn_agent"
            assert run_id
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_agent_selection_changes_only_future_turns(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="first"),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="second"),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    state.agents.create("Reviewer", authority="read_only")
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages", headers=headers, json={"content": "one"}
            )
            await _wait_for_event(client, headers, session_id, "run.completed")
            changed = await client.put(
                f"/v1/sessions/{session_id}/agent",
                headers=headers,
                json={"agent_id": "reviewer"},
            )
            assert response_object(changed)["agent_id"] == "reviewer"
            await client.post(
                f"/v1/sessions/{session_id}/messages", headers=headers, json={"content": "two"}
            )
            events = await _wait_for_event(
                client, headers, session_id, "run.completed", occurrences=2
            )
            messages = [event for event in events if event["type"] == "assistant.message"]
            assert messages[0]["agent_id"] == "default"
            assert messages[-1]["agent_id"] == "reviewer"
            changed_event = next(
                event for event in events if event["type"] == "session.agent.changed"
            )
            assert changed_event["agent_id"] == "reviewer"
            contexts = [event for event in events if event["type"] == "context.compiled"]
            assert contexts[-1]["payload"]["agent_id"] == "reviewer"  # type: ignore[index]
            second_turn = next(
                request
                for request in fake.requests
                if any(
                    message.role == "user" and message.content == "two"
                    for message in request.messages
                )
            )
            assert [tool.name for tool in second_turn.tools] == [
                "ask_user",
                "read_file",
                "list_dir",
                "vcs_inspect",
                "spawn_agent",
                "agent_control",
                "skill_load",
                "memory_search",
                "scar_list",
                "skill_catalog",
                "session_title_set",
                "task_list",
                "task_update",
                "mcp_resource_list",
                "mcp_resource_read",
            ]
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_trust_gate_persists_exact_root_and_can_be_revoked(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            blocked = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "not yet"},
            )
            assert blocked.status_code == 409
            assert response_object(blocked)["error"]["code"] == "working_directory_untrusted"  # type: ignore[index]

            trusted = await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            assert response_object(trusted)["trusted"] is True
            second = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            second_id = str(response_object(second)["id"])
            status = await client.get(f"/v1/sessions/{second_id}/trust", headers=headers)
            assert response_object(status)["trusted"] is True
            revoked = await client.delete(f"/v1/sessions/{second_id}/trust", headers=headers)
            assert response_object(revoked)["trusted"] is False
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_destructive_shell_waits_for_exact_one_shot_denial(tmp_path: Path) -> None:
    fake = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="danger-1",
                        name="shell",
                        arguments_delta='{"command":"rm -rf ../target"}',
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="The action was denied."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "delete target"},
            )
            events = await _wait_for_event(client, headers, session_id, "approval.requested")
            requested = next(event for event in events if event["type"] == "approval.requested")
            payload = requested["payload"]
            assert isinstance(payload, dict)
            approval_id = str(payload["approval_id"])
            request_hash = str(payload["request_hash"])
            stale = await client.post(
                f"/v1/approvals/{approval_id}",
                headers=headers,
                json={"decision": "denied", "request_hash": "0" * 64},
            )
            assert stale.status_code == 409
            denied = await client.post(
                f"/v1/approvals/{approval_id}",
                headers=headers,
                json={"decision": "denied", "request_hash": request_hash},
            )
            assert denied.status_code == 200
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            assert any(event["type"] == "tool.rejected" for event in events)
            repeated = await client.post(
                f"/v1/approvals/{approval_id}",
                headers=headers,
                json={"decision": "approved", "request_hash": request_hash},
            )
            assert repeated.status_code == 409
            assert fake.requests[1].messages[-1].role == "tool"
            assert "human denied" in fake.requests[1].messages[-1].content
    finally:
        await state.runs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_config", "tool_calls", "expected_code"),
    [
        ("max_model_turns_per_user_message = 1", 1, "model_turn_limit"),
        ("max_tool_calls_per_run = 1", 2, "tool_call_limit"),
    ],
)
async def test_agent_loop_limits_are_typed(
    tmp_path: Path, runtime_config: str, tool_calls: int, expected_code: str
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(f"[runtime]\n{runtime_config}\n", encoding="utf-8")
    turn = [StreamEvent(kind=StreamEventKind.STARTED)]
    for index in range(tool_calls):
        turn.append(
            StreamEvent(
                kind=StreamEventKind.TOOL_CALL_DELTA,
                tool_call=ToolCallDelta(
                    index=index,
                    provider_call_id=f"call-{index}",
                    name="unknown_fixture_tool",
                    arguments_delta="{}",
                ),
            )
        )
    turn.append(StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"))
    state = GatewayState.create(paths, providers={"fake": FakeProvider([], turns=[turn])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "keep going"},
            )
            events = await _wait_for_event(client, headers, session_id, "run.failed")
            failure = next(event for event in events if event["type"] == "run.failed")["payload"]
            assert isinstance(failure, dict)
            assert failure["code"] == expected_code
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_agent_active_time_limit_is_typed(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[runtime]\nmax_active_seconds_per_run = 0.02\n", encoding="utf-8")
    state = GatewayState.create(paths, providers={"fake": StallingProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "wait forever"},
            )
            events = await _wait_for_event(client, headers, session_id, "run.failed")
            failure = next(event for event in events if event["type"] == "run.failed")["payload"]
            assert isinstance(failure, dict)
            assert failure["code"] == "active_time_limit"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_creates_each_explicit_memory_layer(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            for layer in ("relationship", "semantic", "episodic"):
                response = await client.post(
                    f"/v1/sessions/{session_id}/memories",
                    headers=headers,
                    json={
                        "layer": layer,
                        "visibility": "workspace",
                        "subject": f"test:{layer}",
                        "predicate": "records_fact",
                        "value": f"A durable {layer} value",
                        "summary": f"An explicit {layer} memory.",
                    },
                )
                assert response.status_code == 201
                body = response_object(response)
                assert body["layer"] == layer
                assert body["status"] == "active"
                assert body["origin_kind"] == "explicit"
                assert body["visibility"] == "workspace"

            listed = await client.get(
                f"/v1/sessions/{session_id}/memories",
                headers=headers,
                params={"status": "active"},
            )
            assert {item["layer"] for item in listed.json()} == {
                "relationship",
                "semantic",
                "episodic",
            }
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_exposes_memory_review_and_promotion(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            session = state.ledger.get_session(session_id)
            evidence = state.ledger.append(
                session_id=session_id,
                agent_id=session.agent_id,
                event_type="user.message",
                payload={"content": "Keep documentation concise."},
            )
            proposed = state.memory.store.create_candidate(
                session=session,
                candidate=MemoryCandidate(
                    layer="relationship",
                    visibility="workspace",
                    subject="user:local",
                    predicate="prefers_documentation_style",
                    value="concise",
                    summary="The user prefers concise documentation.",
                    confidence=0.95,
                    importance=0.9,
                    provenance_event_ids=[evidence.id],
                    evidence_basis="explicit_user",
                ),
                run_id=None,
                origin_kind="automatic",
                activate=False,
                causation_id=evidence.id,
            ).record

            proposals = await client.get(
                f"/v1/sessions/{session_id}/memories",
                headers=headers,
                params={"status": "proposed"},
            )
            assert proposals.status_code == 200
            assert [item["id"] for item in proposals.json()] == [proposed.id]
            proposals_after_first = await client.get(
                f"/v1/sessions/{session_id}/memories",
                headers=headers,
                params={"status": "proposed", "offset": 1},
            )
            assert proposals_after_first.status_code == 200
            assert proposals_after_first.json() == []

            accepted = await client.post(
                f"/v1/sessions/{session_id}/memories/{proposed.id}/transition",
                headers=headers,
                json={"action": "accept"},
            )
            assert accepted.status_code == 200
            assert response_object(accepted)["status"] == "active"

            promoted = await client.post(
                f"/v1/sessions/{session_id}/memories/{proposed.id}/promote",
                headers=headers,
                json={"visibility": "global"},
            )
            assert promoted.status_code == 200
            promoted_body = response_object(promoted)
            assert promoted_body["visibility"] == "global"
            assert promoted_body["status"] == "active"

            search = await client.get(
                f"/v1/sessions/{session_id}/memories",
                headers=headers,
                params={"query": "concise documentation"},
            )
            assert search.status_code == 200
            assert [item["id"] for item in search.json()] == [promoted_body["id"]]

            secret = await client.post(
                f"/v1/sessions/{session_id}/memories/capture",
                headers=headers,
                json={"content": "Remember sk_this_is_a_fake_secret_token_12345"},
            )
            assert secret.status_code == 400
            secret_error = response_object(secret)["error"]
            assert isinstance(secret_error, dict)
            assert secret_error["code"] == "memory_secret_rejected"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_exposes_skill_inspection_and_lifecycle_controls(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    portable_dir = paths.portable_global_skills / "portable-review"
    portable_dir.mkdir(parents=True)
    portable_dir.joinpath("SKILL.md").write_text(
        """---
name: portable-review
description: Review code from the global portable Skill directory.
---
Review the requested code.
""",
        encoding="utf-8",
    )
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            session = state.ledger.get_session(session_id)
            evidence = state.ledger.append(
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="user.message",
                payload={"content": "Inspect files and verify the result."},
            )
            drafted = state.runs.skills.create_draft(
                session=session,
                draft=SkillDraft(
                    id="inspect-files",
                    name="Inspect Files",
                    description="Inspect project files and verify the result.",
                    scope="workspace",
                    tools=["read_file", "list_dir"],
                    triggers=["inspect files"],
                    instructions=(
                        "Read the requested file, list its directory, and verify findings."
                    ),
                ),
                evidence_event_ids=[evidence.id],
                created_by="automatic",
                run_id=None,
                causation_id=evidence.id,
            )
            draft_catalog = await client.get(
                f"/v1/sessions/{session_id}/skills/available", headers=headers
            )
            draft_entry = next(
                item for item in draft_catalog.json() if item["slug"] == "inspect-files"
            )
            assert draft_entry["status"] == "draft"
            assert draft_entry["source"] == "managed"
            state.runs.skills.activate(
                session=session,
                version_id=drafted.version.id,
                causation_id=drafted.events[-1].id,
            )

            listed = await client.get(
                f"/v1/sessions/{session_id}/skills",
                headers=headers,
                params={"query": "inspect files"},
            )
            assert listed.status_code == 200
            assert listed.json()[0]["slug"] == "inspect-files"
            assert listed.json()[0]["source"] == "managed"
            complete_catalog = await client.get(
                f"/v1/sessions/{session_id}/skills", headers=headers
            )
            assert {
                "linux-gui-testing",
                "visual-verification",
                "web-app-debugging",
            } <= {item["slug"] for item in complete_catalog.json()}
            shown = await client.get(
                f"/v1/sessions/{session_id}/skills/inspect-files", headers=headers
            )
            assert shown.status_code == 200
            assert shown.json()["instructions"].startswith("Read the requested")
            pinned = await client.post(
                f"/v1/sessions/{session_id}/skills/inspect-files/pin",
                headers=headers,
                json={},
            )
            assert pinned.status_code == 200
            assert pinned.json()["pinned"] is True
            archived = await client.post(
                f"/v1/sessions/{session_id}/skills/inspect-files/archive",
                headers=headers,
                json={},
            )
            assert archived.status_code == 200
            archived_catalog = await client.get(
                f"/v1/sessions/{session_id}/skills/available", headers=headers
            )
            archived_entry = next(
                item for item in archived_catalog.json() if item["slug"] == "inspect-files"
            )
            assert archived_entry["archived"] is True
            after_archive = await client.get(f"/v1/sessions/{session_id}/skills", headers=headers)
            assert "inspect-files" not in {item["slug"] for item in after_archive.json()}
            restored = await client.post(
                f"/v1/sessions/{session_id}/skills/inspect-files/restore",
                headers=headers,
                json={},
            )
            assert restored.status_code == 200
            history = await client.get(
                f"/v1/sessions/{session_id}/skills/inspect-files/history", headers=headers
            )
            assert history.status_code == 200
            assert len(history.json()) == 1
            deleted = await client.delete(
                f"/v1/sessions/{session_id}/skills/inspect-files", headers=headers
            )
            assert deleted.status_code == 200
            assert deleted.json() == {
                "skill_id": drafted.version.skill_id,
                "slug": "inspect-files",
                "deleted": True,
            }
            after_delete = await client.get(
                f"/v1/sessions/{session_id}/skills/available", headers=headers
            )
            assert "inspect-files" not in {item["slug"] for item in after_delete.json()}
            events = await client.get(f"/v1/sessions/{session_id}/events", headers=headers)
            assert "skill.deleted" in {item["type"] for item in events.json()}

            builtin_delete = await client.delete(
                f"/v1/sessions/{session_id}/skills/visual-verification", headers=headers
            )
            assert builtin_delete.status_code == 409
            assert "read-only" in builtin_delete.text
            portable_delete = await client.delete(
                f"/v1/sessions/{session_id}/skills/portable-review", headers=headers
            )
            assert portable_delete.status_code == 409
            assert "read-only" in portable_delete.text
            jobs = await client.get(f"/v1/sessions/{session_id}/skill-jobs", headers=headers)
            assert jobs.status_code == 200
            assert jobs.json() == []
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_user_invoked_portable_skill_is_loaded_before_the_first_model_request(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    skill_dir = paths.portable_global_skills / "teach"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        """---
name: teach
description: Teach a requested topic.
metadata:
  hames/invocation: user
  hames/argument-hint: "[topic]"
---
Teach $ARGUMENTS with one example.
""",
        encoding="utf-8",
    )
    fake = FakeProvider(
        [
            StreamEvent(kind=StreamEventKind.STARTED),
            StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="lesson"),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]
    )
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            catalog = await client.get(f"/v1/sessions/{session_id}/skills", headers=headers)
            teach = next(item for item in catalog.json() if item["slug"] == "teach")
            assert teach["source"] == "portable"
            assert teach["invocation"] == "user"
            assert teach["argument_hint"] == "[topic]"
            available = await client.get(
                f"/v1/sessions/{session_id}/skills/available/teach", headers=headers
            )
            assert available.status_code == 200
            assert available.json()["created_by"] == "external"
            assert available.json()["instructions"].startswith("Teach $ARGUMENTS")
            fake.turns = [
                [
                    StreamEvent(kind=StreamEventKind.STARTED),
                    StreamEvent(
                        kind=StreamEventKind.TOOL_CALL_DELTA,
                        tool_call=ToolCallDelta(
                            index=0,
                            provider_call_id="reload-selected",
                            name="skill_load",
                            arguments_delta=json.dumps({"id": teach["id"]}),
                        ),
                    ),
                    StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
                ],
                [
                    StreamEvent(kind=StreamEventKind.STARTED),
                    StreamEvent(
                        kind=StreamEventKind.TOOL_CALL_DELTA,
                        tool_call=ToolCallDelta(
                            index=0,
                            provider_call_id="undeclared-script",
                            name="skill_run",
                            arguments_delta=json.dumps(
                                {"id": teach["id"], "script": "missing", "args": []}
                            ),
                        ),
                    ),
                    StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
                ],
                fake.events,
            ]
            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "/teach finite state machines"},
            )
            assert accepted.status_code == 202
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            loaded = next(event for event in events if event["type"] == "skill.loaded")
            assert loaded["payload"]["reason"] == "user_selected"  # type: ignore[index]
            assert "Teach finite state machines with one example." in fake.requests[0].system
            completed = [event for event in events if event["type"] == "tool.completed"]
            assert any("loaded Skill teach" in str(event["payload"]) for event in completed)
            assert any(
                "Skill does not declare that script" in str(event["payload"])
                for event in events
                if event["type"] == "tool.rejected"
            )
            assert "Teach finite state machines with one example." in str(fake.requests[1].messages)

    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_background_shell_is_listed_and_stop_closes_it_with_transcript_events(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[memory]\nenabled = false\n[skills]\nenabled = false\n[evolution]\nenabled = false\n",
        encoding="utf-8",
    )
    provider = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="background-shell",
                        name="shell",
                        arguments_delta=json.dumps({"command": "sleep 30", "background": True}),
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TEXT_DELTA,
                    text="The background terminal is running.",
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Start the watcher in the background."},
            )
            await _wait_for_event(client, headers, session_id, "run.completed")

            listed = await client.get(f"/v1/sessions/{session_id}/terminals", headers=headers)
            terminals = cast(list[dict[str, JsonValue]], listed.json())
            assert len(terminals) == 1
            assert terminals[0]["command"] == "sleep 30"
            assert terminals[0]["status"] == "running"
            health = response_object(await client.get("/v1/health"))
            assert health["active_terminals"] == 1

            stopped = await client.delete(f"/v1/sessions/{session_id}/terminals", headers=headers)
            assert response_object(stopped) == {"closed": 1}
            events = await _wait_for_event(client, headers, session_id, "terminal.stopped")
            terminal_event = next(event for event in events if event["type"] == "terminal.stopped")
            assert terminal_event["payload"]["reason"] == "user_stop"  # type: ignore[index]
            notices: list[str] = []
            for event in events:
                if event["type"] != "runtime.notice":
                    continue
                payload = JSON_OBJECT.validate_python(event["payload"])
                if payload.get("code") not in {
                    "background_terminals.closing",
                    "background_terminals.closed",
                }:
                    continue
                message = payload.get("message")
                assert isinstance(message, str)
                notices.append(message)
            assert notices == [
                "Closing 1 background terminal…",
                "Closed 1 background terminal.",
            ]
            assert response_object(await client.get("/v1/health"))["active_terminals"] == 0
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_agent_can_stop_background_terminals_it_started(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[memory]\nenabled = false\n[skills]\nenabled = false\n[evolution]\nenabled = false\n",
        encoding="utf-8",
    )
    provider = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="background-shell",
                        name="shell",
                        arguments_delta=json.dumps({"command": "sleep 30", "background": True}),
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TEXT_DELTA,
                    text="The background terminal is running.",
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="stop-terminals",
                        name="terminal_stop",
                        arguments_delta="{}",
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Closed the watcher."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Start the watcher in the background."},
            )
            await _wait_for_event(client, headers, session_id, "run.completed")
            listed = await client.get(f"/v1/sessions/{session_id}/terminals", headers=headers)
            assert len(cast(list[object], listed.json())) == 1

            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "The watcher is finished; close it."},
            )
            events = await _wait_for_event(client, headers, session_id, "terminal.stopped")
            terminal_event = next(event for event in events if event["type"] == "terminal.stopped")
            assert terminal_event["payload"]["reason"] == "agent_stop"  # type: ignore[index]
            await _wait_for_event(client, headers, session_id, "run.completed", occurrences=2)
            assert response_object(await client.get("/v1/health"))["active_terminals"] == 0
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_background_shell_natural_exit_captures_output_and_clears_active_state(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[memory]\nenabled = false\n[skills]\nenabled = false\n[evolution]\nenabled = false\n",
        encoding="utf-8",
    )
    provider = FakeProvider(
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="short-background-shell",
                        name="shell",
                        arguments_delta=json.dumps(
                            {"command": "sleep 0.05; printf ready", "background": True}
                        ),
                    ),
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Started."),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
    )
    state = GatewayState.create(paths, providers={"fake": provider})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Run the short task in the background."},
            )
            events = await _wait_for_event(client, headers, session_id, "terminal.completed")
            completed = next(event for event in events if event["type"] == "terminal.completed")
            assert completed["payload"]["exit_code"] == 0  # type: ignore[index]
            assert completed["payload"]["stdout"] == "ready"  # type: ignore[index]
            assert completed["payload"]["reason"] == "exit"  # type: ignore[index]
            listed = await client.get(f"/v1/sessions/{session_id}/terminals", headers=headers)
            assert listed.json() == []
    finally:
        await state.runs.close()


async def _wait_for_event(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    session_id: str,
    event_type: str,
    occurrences: int = 1,
) -> list[dict[str, JsonValue]]:
    events: list[dict[str, JsonValue]] = []
    for _ in range(1000):
        response = await client.get(f"/v1/sessions/{session_id}/events", headers=headers)
        events = EVENT_LIST.validate_python(cast(object, response.json()))
        if sum(event["type"] == event_type for event in events) >= occurrences:
            return events
        await asyncio.sleep(0.01)
    raise AssertionError(f"event did not arrive: {event_type}")


@pytest.mark.asyncio
async def test_correction_scar_and_rule_lifecycle_over_gateway(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)

            corrected = await client.post(
                f"/v1/sessions/{session_id}/correct",
                headers=headers,
                json={"content": "the milestone file is docs/plan.md"},
            )
            assert corrected.status_code == 201
            scar = response_object(corrected)
            assert scar["status"] == "guarded"
            assert scar["detection"] == "explicit_correction"
            assert scar["repair_layer"] == "semantic_memory"
            scar_id = str(scar["id"])

            listed = await client.get(f"/v1/sessions/{session_id}/scars", headers=headers)
            assert [item["id"] for item in listed.json()] == [scar_id]

            inspection = await client.get(
                f"/v1/sessions/{session_id}/scars/{scar_id}/inspection", headers=headers
            )
            lineage = response_object(inspection)
            assert str(lineage["explanation"]).startswith("The user explicitly corrected")
            transitions = cast(list[dict[str, JsonValue]], lineage["transitions"])
            assert any(item.get("event_type") == "scar.recorded" for item in transitions)

            edited = await client.patch(
                f"/v1/sessions/{session_id}/scars/{scar_id}",
                headers=headers,
                json={
                    "title": "Use the documented milestone file",
                    "severity": "medium",
                    "description": "The assistant named the wrong milestone source.",
                    "expected_behavior": "Read docs/plan.md before answering.",
                },
            )
            assert edited.status_code == 200
            edited_scar = response_object(edited)
            assert edited_scar["title"] == "Use the documented milestone file"
            assert edited_scar["severity"] == "medium"
            assert edited_scar["failure_signature"] == scar["failure_signature"]

            deleted = await client.delete(
                f"/v1/sessions/{session_id}/scars/{scar_id}", headers=headers
            )
            assert deleted.status_code == 200
            assert response_object(deleted) == {"scar_id": scar_id, "deleted": True}
            missing = await client.get(
                f"/v1/sessions/{session_id}/scars/{scar_id}", headers=headers
            )
            assert missing.status_code == 404

            # context rule lifecycle: propose -> inactive until approved -> activate
            proposed = await client.post(
                f"/v1/sessions/{session_id}/context-rules",
                headers=headers,
                json={
                    "description": "Status questions must include the current milestone.",
                    "require_source_types": ["memory"],
                    "workspace_paths": [str(tmp_path)],
                },
            )
            assert proposed.status_code == 201
            rule = response_object(proposed)
            assert rule["status"] == "proposed"
            rule_id = str(rule["id"])

            activated = await client.post(
                f"/v1/context-rules/{rule_id}/activate",
                headers=headers,
                json={"reason": "approved by user"},
            )
            assert response_object(activated)["status"] == "active"

            # policy rule lifecycle
            policy_proposed = await client.post(
                f"/v1/sessions/{session_id}/policy-rules",
                headers=headers,
                json={
                    "action": "deny",
                    "pattern": r"curl[^|]*\|\s*(?:ba)?sh",
                    "reason": "no piping remote scripts",
                },
            )
            assert policy_proposed.status_code == 201
            policy_rule = response_object(policy_proposed)
            policy_activated = await client.post(
                f"/v1/policy-rules/{policy_rule['id']}/activate",
                headers=headers,
                json={"reason": "approved"},
            )
            assert response_object(policy_activated)["status"] == "active"
            active_rules = await client.get("/v1/policy-rules?status=active", headers=headers)
            assert len(active_rules.json()) == 1
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_gateway_creates_a_manual_scar(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created_session = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created_session)["id"])
            response = await client.post(
                f"/v1/sessions/{session_id}/scars",
                headers=headers,
                json={
                    "title": "Verify before reporting success",
                    "severity": "high",
                    "scope": "agent",
                    "failure_signature": "reports success without verification",
                    "description": "The assistant reported success without checking the result.",
                    "expected_behavior": "Verify the result before reporting completion.",
                },
            )
            assert response.status_code == 201
            scar = response_object(response)
            assert scar["status"] == "open"
            assert scar["scope"] == "agent"
            assert scar["owner_agent_id"] == "default"
            assert scar["detection"] == "manual"
            assert scar["evidence_event_ids"] == []

            listed = await client.get(f"/v1/sessions/{session_id}/scars", headers=headers)
            assert [item["id"] for item in listed.json()] == [scar["id"]]
            events = await client.get(f"/v1/sessions/{session_id}/events", headers=headers)
            types = [item["type"] for item in events.json()]
            assert "scar.recorded" in types
            assert "scar.opened" in types
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_explicit_dream_bypasses_idle_and_requires_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[runtime]\ndream_idle_seconds = 3600\n"
        "[memory]\nenabled = false\n[skills]\nenabled = false\n"
        "[evolution]\nenabled = false\n",
        encoding="utf-8",
    )
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)), base_url="http://test"
        ) as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            endpoint = f"/v1/sessions/{session_id}/dream"
            assert (await client.post(endpoint)).status_code == 401
            assert (await client.post(endpoint, headers=headers)).status_code == 409
            assert state.ledger.get_session(session_id).title is None
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            state.runs._schedule_dream(session_id, session_id)  # pyright: ignore[reportPrivateUsage]
            pending_idle = state.runs._dream_tasks[session_id]  # pyright: ignore[reportPrivateUsage]
            accepted = await client.post(endpoint, headers=headers)
            assert pending_idle.cancelled()
            assert accepted.status_code == 202
            assert state.ledger.get_session(session_id).title == "Dream"
            assert state.ledger.list_sessions(has_messages=True) == []
            assert [
                session.id
                for session in state.ledger.list_sessions(has_messages=True, include_titled=True)
            ] == [session_id]
            listed = await client.get(
                "/v1/sessions?has_messages=true&include_titled=true", headers=headers
            )
            assert '"Dream"' in listed.text
            dream_id = response_object(accepted)["dream_id"]
            events = await _wait_for_event(client, headers, session_id, "dream.completed")
            dreams = [event for event in events if str(event["type"]).startswith("dream.")]
            assert [event["type"] for event in dreams] == ["dream.started", "dream.completed"]
            assert all(
                JSON_OBJECT.validate_python(event["payload"])["dream_id"] == dream_id
                for event in dreams
            )
            assert not any(event["type"] == "user.message" for event in events)

            state.ledger.update_session_title(session_id, title="My maintenance chat")
            entered = asyncio.Event()

            async def blocked_cleanup(session_id: str) -> int:
                entered.set()
                await asyncio.Event().wait()
                return 0

            monkeypatch.setattr(state.evolution, "dream_cleanup", blocked_cleanup)
            state.runs.evolution_manager = state.evolution
            assert (await client.post(endpoint, headers=headers)).status_code == 202
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert state.ledger.get_session(session_id).title == "My maintenance chat"
            assert (await client.post(endpoint, headers=headers)).status_code == 409
            await state.runs._yield_dream(session_id)  # pyright: ignore[reportPrivateUsage]
            await _wait_for_event(client, headers, session_id, "dream.paused")

            def always_active(session_id: str) -> bool:
                return True

            monkeypatch.setattr(state.runs, "is_session_active", always_active)
            assert (await client.post(endpoint, headers=headers)).status_code == 409
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_first_greeting_persists_title_without_browser_title_request(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": QueueProvider()})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ids: list[str] = []
            for content in ("hi", "hello again"):
                created = await client.post(
                    "/v1/sessions",
                    headers=headers,
                    json={
                        "working_directory": str(tmp_path),
                        "provider": "fake",
                        "model": "fixture",
                    },
                )
                session_id = str(response_object(created)["id"])
                ids.append(session_id)
                await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
                accepted = await client.post(
                    f"/v1/sessions/{session_id}/messages",
                    headers=headers,
                    json={"content": content},
                )
                assert accepted.status_code == 202
                assert state.ledger.get_session(session_id).title == content
                titles = [
                    event
                    for event in state.ledger.replay(session_id)
                    if event.type == "session.title.changed"
                ]
                assert len(titles) == 1
                assert titles[0].payload["title"] == content
            assert ids[0] != ids[1]
            state.ledger.update_session_title(ids[0], title="Authoritative model title")
            assert state.ledger.ensure_session_title(ids[0], "late provisional greeting") is None
            assert state.ledger.get_session(ids[0]).title == "Authoritative model title"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_work_titles_follow_acceptance_and_preserve_existing_titles(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={"working_directory": str(tmp_path), "provider": "fake", "model": "fixture"},
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            for endpoint in ("tasks", "goals/current", "plans/current"):
                assert (
                    await client.get(f"/v1/sessions/{session_id}/{endpoint}", headers=headers)
                ).status_code == 200
            rejected = await client.post(f"/v1/sessions/{session_id}/compact", headers=headers)
            assert rejected.status_code >= 400
            assert state.ledger.get_session(session_id).title is None
            accepted = await client.post(
                f"/v1/sessions/{session_id}/tasks",
                headers=headers,
                json={"text": "Inspect the drawer"},
            )
            assert accepted.status_code < 300, accepted.text
            assert state.ledger.get_session(session_id).title == "Inspect the drawer"
            await client.put(
                f"/v1/sessions/{session_id}/title",
                headers=headers,
                json={"title": "Authored title"},
            )
            await client.post(
                f"/v1/sessions/{session_id}/tasks",
                headers=headers,
                json={"text": "Another task"},
            )
            assert state.ledger.get_session(session_id).title == "Authored title"
            assert not any(
                event.type == "user.message" for event in state.ledger.replay(session_id)
            )
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_active_turn_recovers_before_compile_and_compacts_repeatedly(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[context]\nfallback_window_tokens = 32768\noutput_reserve_tokens = 16384\n"
    )

    def response(text: str, *, tool: bool = False) -> list[StreamEvent]:
        result = [StreamEvent(kind=StreamEventKind.STARTED)]
        result.append(StreamEvent(kind=StreamEventKind.TEXT_DELTA, text=text))
        if tool:
            result.append(
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="read",
                        name="read_file",
                        arguments_delta='{"path":"README.md"}',
                    ),
                )
            )
        result.append(
            StreamEvent(
                kind=StreamEventKind.COMPLETED, finish_reason="tool_calls" if tool else "stop"
            )
        )
        return result

    # Each response independently exceeds the remaining input budget, while
    # still fitting the summarizer's input window. Recovery must fold even the
    # newest completed exchange, then do it again within the very same run.
    fake = FakeProvider(
        [],
        turns=[
            response("first progress " + "x" * 65000, tool=True),
            response("checkpoint one: README inspected; finish the original task"),
            response("second progress " + "y" * 65000, tool=True),
            response("checkpoint two: both inspections completed; finish the original task"),
            response("done"),
        ],
    )
    (tmp_path / "README.md").write_text("fixture")
    state = GatewayState.create(paths, providers={"fake": fake})
    headers = {"Authorization": f"Bearer {state.token}"}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)), base_url="http://test"
        ) as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            sent = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Finish the original task"},
            )
            assert sent.status_code == 202
            events = await _wait_for_event(client, headers, session_id, "run.completed")
            checkpoints = [
                event for event in events if event["type"] == "context.compaction.completed"
            ]
            assert len(checkpoints) == 2
            assert not any(event["type"] == "run.failed" for event in events)
            assert len(fake.requests) == 5
            for request in (fake.requests[2], fake.requests[4]):
                assert request.messages[0].content == "Finish the original task"
            assert "checkpoint two" in fake.requests[4].system
            assert fake.requests[1].metadata["purpose"] == "context_compaction"
            assert fake.requests[3].metadata["purpose"] == "context_compaction"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("recover", "closed"), [(False, False), (True, False), (True, True)])
async def test_orphaned_approval_is_cancelled_after_failure_or_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recover: bool, closed: bool
) -> None:
    from hames.runtime import RunFailure

    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path / "home"), providers={"fake": FakeProvider([])}
    )
    session = state.ledger.create_session(
        working_directory=tmp_path, agent_id="default", provider="fake", model="fixture"
    )
    event = state.ledger.append(
        session_id=session.id,
        agent_id="default",
        event_type="user.message",
        payload={"content": "fixture"},
    )
    run_id = "orphan-fixture"
    approval = state.controls.create_approval(
        session_id=session.id,
        run_id=run_id,
        agent_id="default",
        working_directory=str(tmp_path),
        tool_call_id="fixture-call",
        tool_name="write_file",
        arguments={"path": "never-written", "content": "fixture"},
        request_hash="a" * 64,
        reason="manual mode",
    )

    async def fail(*args: object) -> None:
        raise RunFailure("active_time_limit", "fixture failure")

    try:
        if recover:
            if closed:
                state.ledger.close_session(session.id)
            await state.runs.recover_approvals()
            await state.runs.recover_approvals()  # Recovery is idempotent.
        else:
            monkeypatch.setattr(state.runs, "_execute_run", fail)
            await state.runs._run(run_id, session.id, event)  # pyright: ignore[reportPrivateUsage]
        assert state.controls.get_approval(approval.id).status == "cancelled"
        events = state.ledger.list_events(session.id)
        resolved = [e for e in events if e.type == "approval.resolved"]
        assert len(resolved) == (0 if closed else 1)
        if resolved:
            assert resolved[0].payload["decision"] == "cancelled"
        assert not (tmp_path / "never-written").exists()
    finally:
        await state.runs.close()
