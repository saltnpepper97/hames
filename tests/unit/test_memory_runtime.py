from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.ledger import Ledger
from hames.memory import MemoryStore
from hames.memory_runtime import MemoryManager, memory_submission_tool
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
        if evidence["explicit_capture"]:
            event_id = evidence["evidence_event_ids"][0]
            summary = str(evidence["content"])
        else:
            event_id = evidence["evidence"][0]["event_id"]
            summary = "The user prefers concise documentation."
        arguments = json.dumps(
            {
                "candidates": [
                    {
                        "layer": "relationship",
                        "visibility": "global",
                        "subject": "user:local",
                        "predicate": "prefers_docs",
                        "value": "concise",
                        "summary": summary,
                        "confidence": 0.95,
                        "importance": 0.9,
                        "anchors": [],
                        "provenance_event_ids": [event_id],
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


def test_extraction_tool_schema_is_llama_cpp_compatible() -> None:
    encoded = json.dumps(memory_submission_tool().input_schema)
    assert "$defs" not in encoded
    assert "$ref" not in encoded
    assert '"episodic"' not in encoded


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
        assert event_types.count("memory.job.started") == 1
        handle: Any = manager
        assert handle._worker is not None and not handle._worker.done()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_duplicate_dequeue_does_not_kill_memory_worker(
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
        run_id="run-dup",
        agent_id=session.agent_id,
        event_type="run.started",
        payload={"max_model_turns": 1, "max_tool_calls": 1, "max_active_seconds": 30.0},
        causation_id=user.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="run-dup",
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
        job = await manager.enqueue_run(session.id, "run-dup")
        assert job is not None
        for _ in range(100):
            if MemoryStore(ledger).get_job(job.id).status == "completed":
                break
            await asyncio.sleep(0.01)
        assert MemoryStore(ledger).get_job(job.id).status == "completed"
        handle: Any = manager
        handle._queue.put_nowait(job.id)
        await asyncio.sleep(0.4)
        assert handle._worker is not None and not handle._worker.done()
        assert len(provider.requests) == 1
        started_events = [
            event for event in ledger.list_events(session.id) if event.type == "memory.job.started"
        ]
        assert len(started_events) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_explicit_capture_is_activated_and_attributed(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    source = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="memory.capture.requested",
        payload={"content": "The user prefers concise documentation.", "explicit": True},
    )
    provider = ExtractingProvider()
    manager = MemoryManager(
        ledger=ledger,
        config=HamesConfig(),
        providers={"fake": provider},
        broker=EventBroker(),
    )
    try:
        job = await manager.enqueue_capture(
            session, "The user prefers concise documentation.", source
        )
        for _ in range(100):
            if MemoryStore(ledger).get_job(job.id).status == "completed":
                break
            await asyncio.sleep(0.01)
        record = MemoryStore(ledger).list_visible(session)[0]
        assert record.status == "active"
        assert record.origin_kind == "explicit"
        assert source.id in record.provenance_event_ids
        assert provider.requests[0].metadata["purpose"] == "memory_extraction"
    finally:
        await manager.close()
