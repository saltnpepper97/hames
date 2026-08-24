from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hames.plugin_protocol import PluginProtocolError, spawn_worker
from hames.providers.base import JsonValue

WORKER = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "loopback_worker.py"


@pytest.mark.asyncio
async def test_handshake_tool_execute_and_broker_roundtrip() -> None:
    async def on_broker(method: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        assert method == "project.list"
        assert arguments["path"] == "."
        return {"summary": "listed .", "status": "completed"}

    worker = await spawn_worker(
        [sys.executable, "-u", str(WORKER)],
        timeout_seconds=5,
        on_broker=on_broker,
    )
    try:
        init = await worker.initialize("project-stats", "0.1.0")
        assert init.plugin_id == "project-stats"
        assert init.tools[0].name == "summary"
        plain = await worker.execute_tool("summary", {})
        assert plain.summary == "ok"
        brokered = await worker.execute_tool("summary", {"use_broker": True})
        assert brokered.summary == "listed"
        context = await worker.collect_context("files")
        assert context.sources[0]["id"] == "project-stats"
    finally:
        await worker.shutdown()


@pytest.mark.asyncio
async def test_worker_crash_does_not_raise_outside_the_call() -> None:
    worker = await spawn_worker([sys.executable, "-u", str(WORKER)], timeout_seconds=5)
    try:
        await worker.initialize("project-stats", "0.1.0")
        with pytest.raises(PluginProtocolError):
            await worker.execute_tool("summary", {"crash": True})
    finally:
        await worker.shutdown()


@pytest.mark.asyncio
async def test_worker_timeout_is_bounded() -> None:
    worker = await spawn_worker([sys.executable, "-u", str(WORKER)], timeout_seconds=0.2)
    try:
        await worker.initialize("project-stats", "0.1.0")
        with pytest.raises(PluginProtocolError, match="timed out"):
            await worker.execute_tool("summary", {"sleep": 2})
    finally:
        await worker.shutdown()
