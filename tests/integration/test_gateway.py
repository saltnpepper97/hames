from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import TypeAdapter

from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
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

EVENT_LIST = TypeAdapter(list[dict[str, JsonValue]])


def response_object(response: httpx.Response) -> dict[str, JsonValue]:
    return JSON_OBJECT.validate_python(cast(object, response.json()))


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
            assert health_body["protocol_version"] == 4
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
            assert (
                await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            ).status_code == 200
            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Hi"},
            )
            assert accepted.status_code == 202

            event_types: list[str] = []
            events: list[dict[str, JsonValue]] = []
            for _ in range(100):
                response = await client.get(f"/v1/sessions/{session_id}/events", headers=headers)
                events = EVENT_LIST.validate_python(cast(object, response.json()))
                event_types = [str(event["type"]) for event in events]
                if "run.completed" in event_types:
                    break
                await asyncio.sleep(0.01)
            assert event_types == [
                "session.opened",
                "trust.granted",
                "user.message",
                "run.started",
                "context.compiled",
                "model.requested",
                "model.response.started",
                "model.usage",
                "assistant.reasoning",
                "assistant.message",
                "model.response.completed",
                "run.completed",
            ]
            reasoning = next(event for event in events if event["type"] == "assistant.reasoning")
            answer = next(event for event in events if event["type"] == "assistant.message")
            reasoning_payload = reasoning["payload"]
            answer_payload = answer["payload"]
            assert isinstance(reasoning_payload, dict)
            assert isinstance(answer_payload, dict)
            assert reasoning_payload["content"] == "check "
            assert answer_payload["content"] == "hello"
            assert fake.requests[0].reasoning_effort == "medium"

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
            assert payload == {"content": "partial", "status": "interrupted"}
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
async def test_runtime_failure_is_durable_and_terminal(tmp_path: Path) -> None:
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
            session_id = str(response_object(created)["id"])
            await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            accepted = await client.post(
                f"/v1/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Trigger invalid capsule"},
            )
            assert accepted.status_code == 202

            events: list[dict[str, JsonValue]] = []
            for _ in range(100):
                response = await client.get(f"/v1/sessions/{session_id}/events", headers=headers)
                events = EVENT_LIST.validate_python(cast(object, response.json()))
                if any(event["type"] == "run.failed" for event in events):
                    break
                await asyncio.sleep(0.01)

            assert [event["type"] for event in events][-2:] == [
                "runtime.error",
                "run.failed",
            ]
            failure = events[-1]["payload"]
            assert isinstance(failure, dict)
            assert failure["code"] == "runtime_error"
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
            failure = events[-1]["payload"]
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
            failure = events[-1]["payload"]
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
            failure = events[-1]["payload"]
            assert isinstance(failure, dict)
            assert failure["code"] == "active_time_limit"
    finally:
        await state.runs.close()


async def _wait_for_event(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    session_id: str,
    event_type: str,
) -> list[dict[str, JsonValue]]:
    events: list[dict[str, JsonValue]] = []
    for _ in range(100):
        response = await client.get(f"/v1/sessions/{session_id}/events", headers=headers)
        events = EVENT_LIST.validate_python(cast(object, response.json()))
        if any(event["type"] == event_type for event in events):
            return events
        await asyncio.sleep(0.01)
    raise AssertionError(f"event did not arrive: {event_type}")
