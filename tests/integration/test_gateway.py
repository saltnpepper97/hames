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
    ProviderModel,
    StreamEvent,
    StreamEventKind,
    ToolCallDelta,
    Usage,
)
from hames.providers.base import JSON_OBJECT, JsonValue
from hames.providers.fake import FakeProvider
from hames.skills import SkillDraft

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
async def test_gateway_queues_two_messages_and_promotes_them_fifo(tmp_path: Path) -> None:
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

            for content, position in [("two", 1), ("three", 2)]:
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
                json={"content": "four"},
            )
            assert full.status_code == 409
            assert response_object(full)["error"]["code"] == "session_queue_full"  # type: ignore[index]

            provider.release_first.set()
            events = await _wait_for_event(
                client, headers, session_id, "run.completed", occurrences=3
            )
            users = _user_contents(events)
            assert users == ["one", "two", "three"]
            queue = await client.get(f"/v1/sessions/{session_id}/queue", headers=headers)
            assert response_object(queue)["items"] == []
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
            assert health_body["protocol_version"] == 20
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
            assert accepted.status_code == 202
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
            assert event_types[:14] == [
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
            assert persisted_request["max_tokens"] == 4096

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
                event for event in title_events if event["type"] == "session.title.changed"
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

            plan = response_object(
                await client.get(f"/v1/sessions/{session_id}/plans/current", headers=headers)
            )
            current = plan["current"]
            assert isinstance(current, dict)
            assert current["status"] == "ready"
            assert current["tasks"] == ["Implement lifecycle", "Verify behavior"]
            await _wait_for_event(client, headers, session_id, "run.completed")

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
            events = await _wait_for_event(
                client, headers, session_id, "plan.execution.completed"
            )
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
async def test_plan_execution_stall_fails_visibly_after_three_no_progress_turns(
    tmp_path: Path,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text("[memory]\nenabled = false\n", encoding="utf-8")
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
            events = await _wait_for_event(client, headers, session_id, "plan.execution.failed")
            run_id = str(response_object(executed)["run_id"])
            run_events = [event for event in events if event.get("run_id") == run_id]
            failure = next(event for event in run_events if event["type"] == "run.failed")
            assert failure["payload"]["code"] == "run_stalled"  # type: ignore[index]
            assert sum(
                event["type"] == "run.continuation.requested" for event in run_events
            ) == 2
            assert not any(event["type"] == "run.completed" for event in run_events)
    finally:
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
            assert payload["summary"] == "Keep the earlier requirements and completed verification."
            assert payload["turns_compacted"] == 2
            assert payload["provider"] == "fake"
            assert fake.requests[0].metadata == {"purpose": "context_compaction"}
            assert fake.requests[0].tools == []
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
async def test_automatic_compaction_runs_before_an_over_budget_agent_request(
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
        turns=[response("rolling summary one"), response("rolling summary two"), response("done")],
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
                    payload={"content": f"old-{index}-" + ("x" * 40_000)},
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
            assert payload["passes"] == 2
            assert len(fake.requests) == 3
            assert fake.requests[0].metadata == {"purpose": "context_compaction"}
            assert fake.requests[1].metadata == {"purpose": "context_compaction"}
            assert fake.requests[2].metadata == {}
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
                event for event in events if event["type"] == "session.title.changed"
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
async def test_manual_mode_offers_a_durable_session_approval(tmp_path: Path) -> None:
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
                "read_file",
                "list_dir",
                "skill_load",
                "memory_search",
                "scar_list",
                "skill_catalog",
                "session_title_set",
                "task_list",
                "task_update",
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
                "read_file",
                "list_dir",
                "skill_load",
                "memory_search",
                "scar_list",
                "skill_catalog",
                "session_title_set",
                "task_list",
                "task_update",
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
                        arguments_delta='{"command":"rm -rf target"}',
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
            assert (
                await client.get(f"/v1/sessions/{session_id}/skills", headers=headers)
            ).json() == []
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
            jobs = await client.get(f"/v1/sessions/{session_id}/skill-jobs", headers=headers)
            assert jobs.status_code == 200
            assert jobs.json() == []
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
    for _ in range(100):
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
