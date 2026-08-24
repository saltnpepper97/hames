from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import httpx
import pytest
import uvicorn

from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers import ModelRequest, StreamEvent, StreamEventKind, ToolCallDelta, Usage
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


async def run_hames(environment: Mapping[str, str], *args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        REPOSITORY / "target/debug/hames",
        *args,
        cwd=REPOSITORY,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    return process.returncode or 0, stdout.decode(), stderr.decode()


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
                "HAMES_PROVIDERS__FAKE__ADAPTER": "llama_cpp",
                "HAMES_PROVIDERS__FAKE__BASE_URL": "http://127.0.0.1:1/v1",
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
        repl_export = tmp_path / "repl-audit.md"
        stdout, stderr = await asyncio.wait_for(
            process.communicate(
                (
                    "y\nhello\n/usage\n/inspect\n/context\n"
                    f"/export {repl_export} markdown\n"
                    "/fork\n/session\n/events\n/quit\n"
                ).encode()
            ),
            timeout=10,
        )
        output = stdout.decode()
        assert process.returncode == 0, stderr.decode()
        assert "Thinking" in output
        assert "check" in output
        assert "Hames" in output
        assert "hello from fake" in output
        assert "Forked session" in output
        assert "Fork event" in output
        assert "Estimated input" in output
        assert "Selected sources" in output
        assert "Request hash" in output
        assert "Exported markdown audit transcript" in output
        assert "Derived view only" in repl_export.read_text(encoding="utf-8")
        assert repl_export.stat().st_mode & 0o777 == 0o600

        sessions = state.ledger.list_sessions()
        assert len(sessions) == 2
        root = next(session for session in sessions if session.parent_session_id is None)
        branch = next(session for session in sessions if session.parent_session_id is not None)
        events = state.ledger.list_events(root.id)
        event_types = [event.type for event in events]
        assert "assistant.reasoning" in event_types
        assert "assistant.message" in event_types
        assert "model.usage" in event_types
        replay = state.ledger.replay(branch.id)
        assert any(event.type == "assistant.message" for event in replay)

        code, listed, error = await run_hames(environment, "session", "list", "--json")
        assert code == 0, error
        assert len(json.loads(listed)) == 2

        code, shown, error = await run_hames(environment, "session", "show", branch.id, "--json")
        assert code == 0, error
        assert json.loads(shown)["session"]["parent_session_id"] == root.id

        assistant = next(event for event in events if event.type == "assistant.message")
        code, verified, error = await run_hames(
            environment, "event", "verify", assistant.id, "--json"
        )
        assert code == 0, error
        assert json.loads(verified)["ok"] is True

        code, created, error = await run_hames(environment, "session", "new", "--json")
        assert code == 0, error
        assert json.loads(created)["parent_session_id"] is None

        cli_export = tmp_path / "audit.jsonl"
        code, exported, error = await run_hames(
            environment,
            "session",
            "export",
            branch.id,
            "--format",
            "jsonl",
            "--output",
            str(cli_export),
        )
        assert code == 0, error
        assert "exported jsonl" in exported
        header = json.loads(cli_export.read_text(encoding="utf-8").splitlines()[0])
        assert header["provenance_authority"] == "event-ledger"

        code, _, error = await run_hames(
            environment,
            "session",
            "export",
            branch.id,
            "--format",
            "jsonl",
            "--output",
            str(cli_export),
        )
        assert code != 0
        assert "use --force to overwrite" in error

        code, _, error = await run_hames(
            environment,
            "session",
            "export",
            branch.id,
            "--format",
            "jsonl",
            "--output",
            str(cli_export),
            "--force",
        )
        assert code == 0, error
    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_repl_preserves_tool_preparation_through_completion(tmp_path: Path) -> None:
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
        [],
        turns=[
            [
                StreamEvent(kind=StreamEventKind.STARTED, provider_request_id="tools-1"),
                StreamEvent(kind=StreamEventKind.REASONING_DELTA, text="I will write and read."),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="write-1",
                        name="write_file",
                        arguments_delta=json.dumps(
                            {
                                "workspace": "scratch",
                                "path": "activity.txt",
                                "content": "hello from activity",
                                "create_parents": False,
                            }
                        ),
                    ),
                ),
                StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=1,
                        provider_call_id="read-1",
                        name="read_file",
                        arguments_delta=json.dumps(
                            {"workspace": "scratch", "path": "activity.txt"}
                        ),
                    ),
                ),
                StreamEvent(
                    kind=StreamEventKind.USAGE, usage=Usage(input_tokens=4, output_tokens=3)
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"),
            ],
            [
                StreamEvent(kind=StreamEventKind.STARTED, provider_request_id="tools-2"),
                StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Tool continuity complete."),
                StreamEvent(
                    kind=StreamEventKind.USAGE, usage=Usage(input_tokens=5, output_tokens=2)
                ),
                StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
            ],
        ],
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
                "HAMES_PROVIDERS__FAKE__ADAPTER": "llama_cpp",
                "HAMES_PROVIDERS__FAKE__BASE_URL": "http://127.0.0.1:1/v1",
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
        stdout, stderr = await asyncio.wait_for(
            process.communicate(b"y\nexercise tools\n/quit\n"), timeout=10
        )
        output = stdout.decode()
        assert process.returncode == 0, stderr.decode()
        assert "⬢ Change" in output
        assert "Preparing write" in output
        assert "Checking policy" in output
        assert "Writing" in output
        assert "Wrote" in output
        assert "⬢ Explore" in output
        assert "Preparing read" in output
        assert "Reading" in output
        assert "Read" in output
        assert "Tool continuity complete." in output
        assert "requested write_file" not in output
        assert "running write_file" not in output
    finally:
        server.should_exit = True
        await server_task


class GatedProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__([])
        self.ready = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED, provider_request_id="disconnect-test")
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="still running")
        self.ready.set()
        await self.release.wait()
        yield StreamEvent(kind=StreamEventKind.USAGE, usage=Usage(input_tokens=2, output_tokens=2))
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")


@pytest.mark.asyncio
async def test_sse_disconnect_does_not_cancel_and_durable_events_resume(tmp_path: Path) -> None:
    provider = GatedProvider()
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": provider})
    port = available_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(state), host="127.0.0.1", port=port, log_level="error")
    )
    server_task = asyncio.create_task(server.serve())
    try:
        await wait_for_server(port)
        headers = {"Authorization": f"Bearer {state.token}"}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            created = await client.post(
                "/v1/sessions",
                headers=headers,
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            created.raise_for_status()
            session_id = str(created.json()["id"])
            trusted = await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)
            trusted.raise_for_status()
            cursor = int(created.json().get("sequence", 1))

            async with client.stream(
                "GET",
                "/v1/events",
                headers={**headers, "Last-Event-ID": str(cursor)},
                params={"session_id": session_id},
            ) as response:
                response.raise_for_status()
                lines = response.aiter_lines()
                assert await anext(lines) == ": connected"
                accepted = await client.post(
                    f"/v1/sessions/{session_id}/messages",
                    headers=headers,
                    json={"content": "continue after disconnect"},
                )
                accepted.raise_for_status()
                await asyncio.wait_for(provider.ready.wait(), timeout=2)

            provider.release.set()
            events: list[dict[str, object]] = []
            for _ in range(100):
                events_response = await client.get(
                    f"/v1/sessions/{session_id}/events", headers=headers
                )
                events_response.raise_for_status()
                events = events_response.json()
                if any(event["type"] == "run.completed" for event in events):
                    break
                await asyncio.sleep(0.01)
            assert any(event["type"] == "run.completed" for event in events)
            assert not any(event["type"] == "run.cancelled" for event in events)

            resumed: list[dict[str, object]] = []
            async with client.stream(
                "GET",
                "/v1/events",
                headers={**headers, "Last-Event-ID": str(cursor)},
                params={"session_id": session_id},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    envelope = json.loads(line.removeprefix("data: "))
                    if not envelope.get("durable"):
                        continue
                    event = envelope["event"]
                    resumed.append(event)
                    if event["type"] == "run.completed":
                        break
            assert resumed
            assert all(
                isinstance(event["sequence"], int) and event["sequence"] > cursor
                for event in resumed
            )
            assert resumed[-1]["type"] == "run.completed"
    finally:
        server.should_exit = True
        await server_task
