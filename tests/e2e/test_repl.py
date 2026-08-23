from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn

from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers import StreamEvent, StreamEventKind, Usage
from hames.providers.fake import FakeProvider

REPOSITORY = Path(__file__).resolve().parents[2]


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def wait_for_server(port: int) -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                response = await client.get(f"http://127.0.0.1:{port}/v1/health")
                if response.status_code == 200:
                    return
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.02)
    raise AssertionError("test gateway did not start")


@pytest.mark.asyncio
async def test_rust_repl_through_gateway_and_ledger(tmp_path: Path) -> None:
    build = await asyncio.create_subprocess_exec(
        "cargo",
        "build",
        "--quiet",
        "--locked",
        "--bin",
        "hames",
        cwd=REPOSITORY,
    )
    assert await build.wait() == 0

    provider = FakeProvider(
        [
            StreamEvent(kind=StreamEventKind.STARTED, provider_request_id="e2e-request"),
            StreamEvent(kind=StreamEventKind.REASONING_DELTA, text="check"),
            StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="hello from fake"),
            StreamEvent(kind=StreamEventKind.USAGE, usage=Usage(input_tokens=3, output_tokens=4)),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]
    )
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": provider})
    port = available_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(state), host="127.0.0.1", port=port, log_level="error")
    )
    server_task = asyncio.create_task(server.serve())
    try:
        await wait_for_server(port)
        environment = os.environ.copy()
        environment.update(
            {
                "HAMES_HOME": str(paths.root),
                "HAMES_GATEWAY__PORT": str(port),
                "HAMES_RUNTIME__DEFAULT_PROVIDER": "fake",
                "HAMES_PROVIDERS__FAKE__MODEL": "fixture",
            }
        )
        process = await asyncio.create_subprocess_exec(
            REPOSITORY / "target/debug/hames",
            cwd=REPOSITORY,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(b"hello\n/quit\n"), timeout=10)
        output = stdout.decode()
        assert process.returncode == 0, stderr.decode()
        assert "thinking> check" in output
        assert "assistant> hello from fake" in output

        sessions = state.ledger.list_sessions()
        assert len(sessions) == 1
        events = state.ledger.list_events(sessions[0].id)
        event_types = [event.type for event in events]
        assert "assistant.reasoning" in event_types
        assert "assistant.message" in event_types
        assert "model.usage" in event_types
    finally:
        server.should_exit = True
        await server_task
