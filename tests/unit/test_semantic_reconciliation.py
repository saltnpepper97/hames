from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.ledger import Ledger
from hames.memory import MemoryCandidate, MemoryRecord, MemoryStore, SemanticDecision
from hames.memory_runtime import MemoryManager
from hames.paths import HamesPaths
from hames.providers import ModelRequest, ProviderModel, StreamEvent, StreamEventKind, ToolCallDelta


class ReviewingProvider:
    profile_id = adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ProviderModel]:
        return []

    async def aclose(self) -> None:
        pass

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        memories = json.loads(request.messages[0].content)["memories"]
        by_value = {memory["value"]: memory["id"] for memory in memories}
        # Representative reviewer output: paraphrases, a supported correction, and a run recap.
        decisions = [
            {
                "memory_id": by_value["legacy path"],
                "replacement_id": by_value["current path"],
                "reason": "superseded",
                "explanation": "Newer memory explicitly moves the workspace.",
                "confidence": 0.99,
            },
            {
                "memory_id": by_value["same durable fact paraphrased"],
                "replacement_id": by_value["durable fact"],
                "reason": "redundant",
                "explanation": "The surviving fact contains the same useful information.",
                "confidence": 0.99,
            },
            {
                "memory_id": by_value["143 tests passed yesterday"],
                "replacement_id": None,
                "reason": "transient",
                "explanation": "One run check count provides no durable knowledge.",
                "confidence": 0.99,
            },
        ]
        yield StreamEvent(kind=StreamEventKind.STARTED)
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call=ToolCallDelta(
                index=0,
                provider_call_id="review",
                name="reconcile_semantic_memory",
                arguments_delta=json.dumps({"decisions": decisions}),
            ),
        )
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")


@pytest.mark.asyncio
async def test_dream_reviews_old_semantics_and_preserves_enduring_facts(
    hames_paths: HamesPaths,
    tmp_path: Path,
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path, agent_id="default", provider="fake", model="fixture"
    )
    store = MemoryStore(ledger)
    records: list[MemoryRecord] = []
    values = [
        "legacy path",
        "current path",
        "same durable fact paraphrased",
        "durable fact",
        "143 tests passed yesterday",
        "Enduring architecture boundary",
        "Unresolved restore bug",
    ]
    for index, value in enumerate(values):
        source = ledger.append(
            session_id=session.id, event_type="user.message", payload={"content": value}
        )
        candidate = MemoryCandidate(
            layer="semantic",
            visibility="workspace",
            subject=f"fact-{index}",
            predicate="describes",
            value=value,
            summary=value,
            confidence=0.95,
            importance=0.8,
            provenance_event_ids=[source.id],
            evidence_basis="successful_tool",
        )
        record = store.create_candidate(
            session=session,
            candidate=candidate,
            run_id=None,
            origin_kind="automatic",
            activate=True,
            causation_id=source.id,
        ).record
        with ledger.database.connect() as connection:
            connection.execute(
                "UPDATE memory_records SET created_at = ? WHERE id = ?",
                ((datetime.now(UTC) - timedelta(days=300 - index)).isoformat(), record.id),
            )
        records.append(store.get(record.id))
    provider = ReviewingProvider()
    manager = MemoryManager(
        ledger=ledger, config=HamesConfig(), providers={"fake": provider}, broker=EventBroker()
    )
    assert (
        await manager.dream_cleanup(
            session.id, since=datetime.now(UTC) - timedelta(days=1), causation_id=session.id
        )
        == 3
    )
    assert [store.get(record.id).status for record in records] == [
        "superseded",
        "active",
        "superseded",
        "active",
        "retracted",
        "active",
        "active",
    ]
    assert store.get(records[0].id).superseded_by_id == records[1].id
    assert store.get(records[4].id).provenance_event_ids == records[4].provenance_event_ids
    assert len(provider.requests) == 1
    assert "Age and lack of recent use are NOT evidence" in provider.requests[0].system
    assert provider.requests[0].metadata["purpose"] == "memory_reconciliation"
    assert "memory.retracted" in [event.type for event in ledger.replay(session.id)]

    # Never apply a model's fabricated id or a replacement cycle.
    with pytest.raises(ValueError, match="outside"):
        store.reconcile_semantic(
            session,
            records,
            [
                SemanticDecision(
                    memory_id="missing",
                    reason="transient",
                    explanation="Fabricated nonexistent record",
                    confidence=0.99,
                )
            ],
            causation_id=session.id,
        )
    with pytest.raises(ValueError, match="survive"):
        store.reconcile_semantic(
            session,
            records,
            [
                SemanticDecision(
                    memory_id=records[5].id,
                    replacement_id=records[5].id,
                    reason="redundant",
                    explanation="Invalid self replacement",
                    confidence=0.99,
                )
            ],
            causation_id=session.id,
        )
    # A model decision made against stale evidence must not undo a concurrent edit/retraction.
    store.transition(
        session=session, memory_id=records[5].id, action="retract", reason="user correction"
    )
    assert (
        store.reconcile_semantic(
            session,
            records,
            [
                SemanticDecision(
                    memory_id=records[5].id,
                    replacement_id=records[6].id,
                    reason="redundant",
                    explanation="Stale model review output",
                    confidence=0.99,
                )
            ],
            causation_id=session.id,
        )
        == ()
    )

    with pytest.raises(ValueError, match="same scope"):
        store.reconcile_semantic(
            session,
            [records[5], records[6].model_copy(update={"visibility": "global"})],
            [
                SemanticDecision(
                    memory_id=records[5].id,
                    replacement_id=records[6].id,
                    reason="redundant",
                    explanation="Invalid cross scope consolidation",
                    confidence=0.99,
                )
            ],
            causation_id=session.id,
        )
    with pytest.raises(ValueError, match="explicitly captured"):
        store.reconcile_semantic(
            session,
            [records[6].model_copy(update={"origin_kind": "explicit"})],
            [
                SemanticDecision(
                    memory_id=records[6].id,
                    reason="transient",
                    explanation="Do not discard an explicit capture",
                    confidence=0.99,
                )
            ],
            causation_id=session.id,
        )
    with pytest.raises(ValueError, match="validity"):
        store.reconcile_semantic(
            session,
            [records[6]],
            [
                SemanticDecision(
                    memory_id=records[6].id,
                    reason="expired",
                    explanation="Age alone does not establish expiry",
                    confidence=0.99,
                )
            ],
            causation_id=session.id,
        )
