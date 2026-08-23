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
from hames.providers import ModelRequest, ProviderModel, StreamEvent, StreamEventKind, Usage
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
            assert response_object(health)["protocol_version"] == 2
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
                if "model.response.completed" in event_types:
                    break
                await asyncio.sleep(0.01)
            assert event_types == [
                "session.opened",
                "user.message",
                "context.compiled",
                "model.requested",
                "model.response.started",
                "model.usage",
                "assistant.reasoning",
                "assistant.message",
                "model.response.completed",
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
    finally:
        await state.runs.close()


class TwoModelProvider(FakeProvider):
    async def list_models(self) -> list[ProviderModel]:
        models = await super().list_models()
        return [models[0], models[0].model_copy(update={"id": "other"})]


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
                if any(event["type"] == "model.response.failed" for event in events):
                    break
                await asyncio.sleep(0.01)

            assert [event["type"] for event in events][-2:] == [
                "runtime.error",
                "model.response.failed",
            ]
            failure = events[-1]["payload"]
            assert isinstance(failure, dict)
            assert failure["code"] == "runtime_error"
    finally:
        await state.runs.close()
