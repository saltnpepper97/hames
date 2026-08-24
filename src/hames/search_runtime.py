"""Gateway-side MCP host for the bundled web search server."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any, cast

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import TextContent
from pydantic import BaseModel, ConfigDict, Field

from hames.config import HamesConfig
from hames.paths import HamesPaths
from hames.providers import ToolDefinition
from hames.providers.base import JsonValue
from hames.search_service import SearchService, SearchSetupState, SearchStatus

MCP_PROTOCOL_VERSION = "2026-07-28"
WEB_TOOL_NAMES = frozenset({"web_search", "web_fetch"})


class SearchRuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: SearchStatus
    mcp_status: str
    protocol_version: str = ""
    tools: list[str] = Field(default_factory=list)
    error: str = ""


@dataclass(frozen=True, slots=True)
class McpToolResult:
    failed: bool
    content: str
    structured_data: dict[str, JsonValue]


class SearchMcpManager:
    """Discover and invoke the Hames-owned MCP server over managed stdio."""

    def __init__(self, paths: HamesPaths, config: HamesConfig) -> None:
        self.paths = paths
        self.config = config
        self.service = SearchService(paths)
        self._definitions: dict[str, ToolDefinition] = {}
        self._protocol_version = ""
        self._error = ""
        self._lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return WEB_TOOL_NAMES.issubset(self._definitions)

    def names(self) -> set[str]:
        return set(self._definitions) if self.ready else set()

    def definition(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name) if self.ready else None

    async def start(self) -> SearchRuntimeStatus:
        async with self._lock:
            if self.ready:
                return self.status()
            service = await asyncio.to_thread(self.service.ensure_running)
            if service.status != "ready":
                self._error = service.error
                return self.status(service=service)
            state = self.service.load_state()
            if state is None:  # pragma: no cover - guarded by ready status
                self._error = "search setup state disappeared"
                return self.status(service=service)
            try:
                async with self._client(state) as client:
                    protocol_version = self._require_modern(client)
                    listed = await client.list_tools(cache_mode="refresh")
                    definitions = {
                        tool.name: ToolDefinition(
                            name=tool.name,
                            description=tool.description or "",
                            input_schema=cast(dict[str, JsonValue], tool.input_schema),
                        )
                        for tool in listed.tools
                        if tool.name in WEB_TOOL_NAMES
                    }
                missing = sorted(WEB_TOOL_NAMES - definitions.keys())
                if missing:
                    raise RuntimeError(f"search MCP did not advertise: {', '.join(missing)}")
            except BaseException as exc:
                self._error = str(exc)
                self._definitions = {}
                self._protocol_version = ""
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return self.status(service=service)
            self._definitions = definitions
            self._protocol_version = protocol_version
            self._error = ""
            return self.status(service=service)

    async def call(self, name: str, arguments: dict[str, JsonValue]) -> McpToolResult:
        if name not in self._definitions:
            return McpToolResult(True, "web search is unavailable", {})
        state = self.service.load_state()
        if state is None:
            return McpToolResult(True, "web search setup is unavailable", {})
        result = None
        failure: Exception | None = None
        for _attempt in range(2):
            try:
                async with self._client(state) as client:
                    self._require_modern(client)
                    result = await client.call_tool(
                        name,
                        cast(dict[str, Any], arguments),
                        read_timeout_seconds=(
                            self.config.web.fetch_timeout_seconds
                            if name == "web_fetch"
                            else self.config.web.search_timeout_seconds
                        )
                        + 5,
                    )
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = exc
        if result is None:
            self._error = str(failure or "unknown MCP failure")
            return McpToolResult(True, f"{name} failed: {self._error}", {})
        text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
        structured = result.structured_content
        if isinstance(structured, dict):
            structured_data = cast(dict[str, JsonValue], structured)
        else:
            structured_data = {}
        return McpToolResult(bool(result.is_error), text, structured_data)

    async def close(self) -> None:
        async with self._lock:
            self._definitions = {}
            self._protocol_version = ""

    def _client(self, state: SearchSetupState) -> Client:
        environment = {
            "HAMES_SEARXNG_URL": self.service.url(state),
            "HAMES_SEARCH_LIMIT": str(self.config.web.search_limit),
            "HAMES_SAFE_SEARCH": self.config.web.safe_search,
            "HAMES_SEARCH_TIMEOUT": str(self.config.web.search_timeout_seconds),
            "HAMES_FETCH_TIMEOUT": str(self.config.web.fetch_timeout_seconds),
            "HAMES_FETCH_MAX_BYTES": str(self.config.web.fetch_max_bytes),
            "HAMES_FETCH_MAX_CHARS": str(self.config.web.fetch_max_chars),
        }
        return Client(
            StdioServerParameters(
                command=sys.executable,
                args=["-m", "hames.search_mcp"],
                env=environment,
                cwd=os.fspath(self.paths.root),
            ),
            mode="auto",
            read_timeout_seconds=max(
                self.config.web.search_timeout_seconds,
                self.config.web.fetch_timeout_seconds,
            )
            + 5,
        )

    @staticmethod
    def _require_modern(client: Client) -> str:
        protocol_version = client.protocol_version
        if protocol_version != MCP_PROTOCOL_VERSION:
            raise RuntimeError(
                f"search MCP negotiated {protocol_version}; expected {MCP_PROTOCOL_VERSION}"
            )
        return protocol_version

    def status(self, *, service: SearchStatus | None = None) -> SearchRuntimeStatus:
        current = service or self.service.status(probe=False)
        if self.ready:
            mcp_status = "ready"
        elif current.status in {"unconfigured", "disabled"}:
            mcp_status = current.status
        else:
            mcp_status = "degraded"
        return SearchRuntimeStatus(
            service=current,
            mcp_status=mcp_status,
            protocol_version=self._protocol_version,
            tools=sorted(self.names()),
            error=self._error or current.error,
        )
