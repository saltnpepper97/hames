from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.ledger import Ledger
from hames.memory import MemoryStore
from hames.memory_runtime import MemoryManager
from hames.paths import HamesPaths
from hames.providers import ModelRequest, ProviderModel, StreamEvent, StreamEventKind, ToolCallDelta


class ExtractingProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ProviderModel]:
        return [ProviderModel(id="fixture", provider="fake")]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        evidence = json.loads(request.messages[0].content)
        user = evidence["evidence"][0]
        arguments = json.dumps(
            {
                "candidates": [
                    {
                        "layer": "relationship",
                        "visibility": "global",
                        "subject": "user:local",
                        "predicate": "prefers_docs",
                        "value": "concise",
                        "summary": "The user prefers concise documentation.",
                        "confidence": 0.95,
                        "importance": 0.9,
                        "anchors": [],
                        "provenance_event_ids": [user["event_id"]],
                        "evidence_basis": "explicit_user",
                    }
                ]
            }
        )
        yield StreamEvent(kind=StreamEventKind.STARTED, provider_request_id="memory-1")
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call=ToolCallDelta(
                index=0,
                provider_call_id="memory-call",
                name="submit_memory_candidates",
                arguments_delta=arguments,
            ),
        )
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_background_extraction_activates_important_user_memory(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "I prefer concise documentation."},
    )
    started = ledger.append(
        session_id=session.id,
        run_id="run-1",
        agent_id=session.agent_id,
        event_type="run.started",
        payload={"max_model_turns": 1, "max_tool_calls": 1, "max_active_seconds": 30.0},
        causation_id=user.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="run-1",
        agent_id=session.agent_id,
        event_type="assistant.message",
        payload={"content": "Understood.", "status": "completed"},
        causation_id=started.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="run-1",
        agent_id=session.agent_id,
        event_type="run.completed",
        payload={"model_turns": 1, "tool_calls": 0, "active_seconds": 0.1},
        causation_id=started.id,
    )
    provider = ExtractingProvider()
    manager = MemoryManager(
        ledger=ledger,
        config=HamesConfig(),
        providers={"fake": provider},
        broker=EventBroker(),
    )
    try:
        job = await manager.enqueue_run(session.id, "run-1")
        assert job is not None
        for _ in range(100):
            if MemoryStore(ledger).get_job(job.id).status == "completed":
                break
            await asyncio.sleep(0.01)
        assert MemoryStore(ledger).get_job(job.id).status == "completed"
        records = MemoryStore(ledger).list_visible(session)
        assert len(records) == 1
        assert records[0].layer == "relationship"
        assert records[0].status == "active"
        assert provider.requests[0].metadata["purpose"] == "memory_extraction"
        event_types = [event.type for event in ledger.list_events(session.id)]
        assert "memory.job.completed" in event_types
        assert "memory.accepted" in event_types
    finally:
        await manager.close()
