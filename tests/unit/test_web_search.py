from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from hames.config import load_config
from hames.paths import HamesPaths
from hames.providers import ToolDefinition
from hames.providers.base import JsonValue
from hames.search_mcp import (
    validate_web_destination,
    web_fetch,
    web_search,
)
from hames.search_runtime import MCP_PROTOCOL_VERSION, SearchMcpManager
from hames.search_service import SEARXNG_IMAGE, SearchService, SearchSetupState
from hames.tools import SearchCallOutcome, ToolRegistry


@asynccontextmanager
async def _json_server(payload: Mapping[str, object]) -> AsyncGenerator[str]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = json.dumps(payload).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = cast(Any, server.sockets[0]).getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


def test_declined_search_setup_is_private_and_persistent(
    hames_paths: HamesPaths,
) -> None:
    status = SearchService(hames_paths).setup(enabled=False)

    assert status.status == "disabled"
    assert hames_paths.search_state.stat().st_mode & 0o777 == 0o600
    state = SearchService(hames_paths).load_state()
    assert state is not None
    assert state.configured
    assert not state.enabled
    assert state.image == SEARXNG_IMAGE


def test_search_setup_never_reuses_the_configured_gateway_port(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    hames_paths.config_file.write_text("[gateway]\nport = 7412\n", encoding="utf-8")

    SearchService(hames_paths).setup(enabled=False)

    state = SearchService(hames_paths).load_state()
    assert state is not None
    assert state.port != 7412


def test_enabled_setup_degrades_without_installing_a_container_runtime(
    hames_paths: HamesPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SearchService(hames_paths)
    monkeypatch.setattr(service, "_detect_runtime", lambda: "")

    status = service.setup(enabled=True)

    assert status.status == "degraded"
    assert "Docker or Podman" in status.error
    assert service.load_state() is not None


def test_failed_image_update_restores_the_previous_container_pin(
    hames_paths: HamesPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    hames_paths.ensure_foundation()
    previous = "docker.io/searxng/searxng@sha256:" + "1" * 64
    state = SearchSetupState(
        enabled=True,
        runtime="podman",
        port=7412,
        image=previous,
        container_name="hames-searxng-fixture",
    )
    hames_paths.search_service.mkdir(mode=0o700, parents=True, exist_ok=True)
    hames_paths.search_state.write_text(state.model_dump_json(), encoding="utf-8")
    service = SearchService(hames_paths)

    def true(_state: SearchSetupState) -> bool:
        return True

    def image(_state: SearchSetupState) -> str:
        return previous

    def remove(_state: SearchSetupState) -> None:
        return None

    def run(
        command: list[str], *, timeout: float, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del command, timeout, check
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(service, "_container_exists", true)
    monkeypatch.setattr(service, "_container_running", true)
    monkeypatch.setattr(service, "_container_image", image)
    monkeypatch.setattr(service, "_remove_container", remove)
    monkeypatch.setattr(service, "_wait_healthy", true)
    monkeypatch.setattr(service, "_run", run)

    def create(_state: SearchSetupState, *, image: str | None = None) -> None:
        if image is None:
            raise RuntimeError("fixture update failure")
        assert image == previous

    monkeypatch.setattr(service, "_create_container", create)

    result = service.update()

    assert result.status == "degraded"
    restored = service.load_state()
    assert restored is not None
    assert restored.image == previous


@pytest.mark.asyncio
async def test_search_normalizes_and_bounds_searxng_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "results": [
            {
                "title": f"Result {index}",
                "url": f"https://example.com/{index}",
                "content": f"Snippet {index}",
                "engines": ["fixture"],
                "score": 1 / (index + 1),
            }
            for index in range(5)
        ],
        "unresponsive_engines": [["other", "timeout"]],
    }
    async with _json_server(payload) as url:
        monkeypatch.setenv("HAMES_SEARXNG_URL", url)
        result = await web_search("hames", limit=2)

    assert result["result_count"] == 2
    results = cast(list[dict[str, object]], result["results"])
    assert results[0]["rank"] == 1
    assert results[0]["url"] == "https://example.com/0"
    assert result["engine_failures"] == [["other", "timeout"]]


@pytest.mark.asyncio
async def test_fetch_extracts_html_and_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(
        _: str, *, max_bytes: int, request_timeout: float
    ) -> tuple[str, str, bytes]:
        assert max_bytes >= 65_536
        assert request_timeout > 0
        body = (
            b"<html><head><title>Fixture</title></head><body><main>"
            b"<p>Hello readable world.</p></main></body></html>"
        )
        return "https://example.com/final", "text/html", body

    monkeypatch.setattr("hames.search_mcp._fetch_public", fake_fetch)
    result = await web_fetch("https://example.com/start")

    assert result["final_url"] == "https://example.com/final"
    assert result["title"] == "Fixture"
    assert "Hello readable world" in cast(str, result["content"])
    assert result["truncated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@example.com",
        "http://127.0.0.1",
        "http://169.254.169.254/latest/meta-data",
        "https://[::1]",
        "https://example.com:8443",
    ],
)
async def test_fetch_rejects_unsafe_destinations(url: str) -> None:
    with pytest.raises(ValueError):
        await validate_web_destination(httpx.URL(url))


@pytest.mark.asyncio
async def test_mcp_server_negotiates_modern_protocol_and_advertises_web_tools() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "hames.search_mcp"],
        env={"HAMES_SEARXNG_URL": "http://127.0.0.1:9"},
    )
    async with Client(parameters, mode="auto") as client:
        listed = await client.list_tools(cache_mode="refresh")

        assert client.protocol_version == MCP_PROTOCOL_VERSION
        assert {tool.name for tool in listed.tools} == {"web_search", "web_fetch"}


class _FakeOutcome:
    failed = False
    content = "{}"

    @property
    def structured_data(self) -> dict[str, JsonValue]:
        return {}


class _FakeSearch:
    ready = True

    def definition(self, name: str) -> ToolDefinition:
        return ToolDefinition(name=name, description=name, input_schema={"type": "object"})

    async def call(self, name: str, arguments: dict[str, JsonValue]) -> SearchCallOutcome:
        del name, arguments
        return _FakeOutcome()


def test_ready_search_tools_join_the_core_registry() -> None:
    registry = ToolRegistry(search=_FakeSearch())

    assert {"web_search", "web_fetch"}.issubset(registry.names())
    assert {definition.name for definition in registry.definitions()}.issuperset(
        {"web_search", "web_fetch"}
    )


@pytest.mark.asyncio
async def test_unconfigured_search_manager_stays_disabled(hames_paths: HamesPaths) -> None:
    manager = SearchMcpManager(hames_paths, load_config(hames_paths))

    status = await manager.start()

    assert status.mcp_status == "unconfigured"
    assert not manager.ready
    assert manager.names() == set()
    await manager.close()


@pytest.mark.skipif(
    os.environ.get("HAMES_TEST_SEARXNG") != "1",
    reason="set HAMES_TEST_SEARXNG=1 to exercise the managed container",
)
@pytest.mark.asyncio
async def test_managed_searxng_container_smoke(hames_paths: HamesPaths) -> None:
    service = SearchService(hames_paths)
    manager = SearchMcpManager(hames_paths, load_config(hames_paths))
    try:
        assert service.setup(enabled=True).status == "ready"
        runtime = await manager.start()
        assert runtime.protocol_version == MCP_PROTOCOL_VERSION
        result = await manager.call("web_search", {"query": "SearXNG", "limit": 1})
        assert not result.failed
        assert "result_count" in result.structured_data
    finally:
        await manager.close()
        service.setup(enabled=False)
        service.remove_managed_container(remove_cache=True)
