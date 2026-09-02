"""Persistent external MCP server registry and gateway-side client host."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import (  # pyright: ignore[reportPrivateImportUsage]
    create_mcp_http_client,
)
from mcp_types import (
    AudioContent,
    CallToolResult,
    ImageContent,
    LoggingMessageNotification,
    Resource,
    ResourceLink,
    ResourceTemplate,
    TextContent,
    TextResourceContents,
    Tool,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hames.database import Database
from hames.ledger import utc_now
from hames.providers import ToolDefinition
from hames.providers.base import JsonValue
from hames.tools import ToolContext

MCP_SERVER_ID = re.compile(r"[a-z][a-z0-9-]{0,62}")
MCP_TOOL_PREFIX = "mcp__"
_SAFE_TOOL_COMPONENT = re.compile(r"[^A-Za-z0-9_-]+")
_BASE_ENVIRONMENT = frozenset({"HOME", "PATH", "USER", "LOGNAME", "LANG", "TMPDIR"})
_CONNECT_TIMEOUT_SECONDS = 10.0
_REQUEST_TIMEOUT_SECONDS = 120.0

NoticeSink = Callable[[str, str, str, bool, str | None], Awaitable[None]]


class McpModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class McpServerSpec(McpModel):
    id: str
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list, max_length=128)
    cwd: str | None = None
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if MCP_SERVER_ID.fullmatch(value) is None:
            raise ValueError("MCP server id must match [a-z][a-z0-9-]{0,62}")
        return value

    @field_validator("env", "headers")
    @classmethod
    def valid_environment_references(cls, values: dict[str, str]) -> dict[str, str]:
        for target, source in values.items():
            if not target or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", target) is None:
                raise ValueError(f"invalid environment/header name: {target}")
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source) is None:
                raise ValueError(f"invalid source environment variable: {source}")
        return values

    @model_validator(mode="after")
    def valid_transport(self) -> McpServerSpec:
        if self.transport == "stdio":
            if not self.command or self.url is not None or self.headers:
                raise ValueError("stdio MCP servers require command and do not accept URL/headers")
            if self.cwd is not None:
                cwd = Path(self.cwd).expanduser().resolve(strict=False)
                if not cwd.is_dir():
                    raise ValueError(f"MCP working directory does not exist: {cwd}")
                self.cwd = str(cwd)
        else:
            if self.url is None or self.command is not None or self.args or self.cwd or self.env:
                raise ValueError("HTTP MCP servers require URL and do not accept command/cwd/env")
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("MCP URL must be absolute HTTP(S)")
            if parsed.username or parsed.password:
                raise ValueError("MCP URL must not contain credentials")
        return self


class McpToolView(McpModel):
    name: str
    exposed_name: str
    description: str = ""
    read_only: bool = False
    destructive: bool = False


class McpResourceView(McpModel):
    name: str
    uri: str
    description: str = ""
    mime_type: str = ""
    template: bool = False


class McpServerView(McpModel):
    id: str
    transport: Literal["stdio", "http"]
    enabled: bool
    status: Literal["disabled", "connecting", "ready", "degraded"] = "disabled"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    protocol_version: str = ""
    server_name: str = ""
    server_version: str = ""
    tools: list[McpToolView] = Field(default_factory=lambda: list[McpToolView]())
    resources: list[McpResourceView] = Field(default_factory=lambda: list[McpResourceView]())
    resource_templates: list[McpResourceView] = Field(
        default_factory=lambda: list[McpResourceView]()
    )
    active_calls: int = 0
    error: str = ""


@dataclass(frozen=True, slots=True)
class McpCallOutcome:
    failed: bool
    summary: str
    content: str
    structured_data: dict[str, JsonValue]
    truncated: bool
    blob_references: list[str]


@dataclass(slots=True)
class _Handle:
    spec: McpServerSpec
    stack: AsyncExitStack
    client: Client
    status: Literal["connecting", "ready", "degraded"] = "connecting"
    protocol_version: str = ""
    server_name: str = ""
    server_version: str = ""
    tools: dict[str, Tool] = field(default_factory=lambda: dict[str, Tool]())
    exposed_tools: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    resources: list[McpResourceView] = field(default_factory=lambda: list[McpResourceView]())
    templates: list[McpResourceView] = field(default_factory=lambda: list[McpResourceView]())
    active_calls: int = 0
    error: str = ""


class McpStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add(self, spec: McpServerSpec) -> None:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO mcp_servers(
                    id, transport, enabled, command, args_json, cwd, url,
                    env_json, headers_json, created_at, updated_at
                ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.id,
                    spec.transport,
                    spec.command,
                    json.dumps(spec.args, separators=(",", ":")),
                    spec.cwd,
                    spec.url,
                    json.dumps(spec.env, separators=(",", ":"), sort_keys=True),
                    json.dumps(spec.headers, separators=(",", ":"), sort_keys=True),
                    now,
                    now,
                ),
            )

    def list(self) -> list[tuple[McpServerSpec, bool]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM mcp_servers ORDER BY id").fetchall()
        return [self._record(row) for row in rows]

    def get(self, server_id: str) -> tuple[McpServerSpec, bool]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_servers WHERE id = ?", (server_id,)
            ).fetchone()
        if row is None:
            raise KeyError(server_id)
        return self._record(row)

    def set_enabled(self, server_id: str, enabled: bool) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE mcp_servers SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), utc_now(), server_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(server_id)

    def remove(self, server_id: str) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        if cursor.rowcount == 0:
            raise KeyError(server_id)

    @staticmethod
    def _record(row: Any) -> tuple[McpServerSpec, bool]:
        return (
            McpServerSpec(
                id=str(row["id"]),
                transport=cast(Literal["stdio", "http"], str(row["transport"])),
                command=row["command"],
                args=json.loads(str(row["args_json"])),
                cwd=row["cwd"],
                url=row["url"],
                env=json.loads(str(row["env_json"])),
                headers=json.loads(str(row["headers_json"])),
            ),
            bool(row["enabled"]),
        )


class McpManager:
    def __init__(self, database: Database, notice: NoticeSink) -> None:
        self.store = McpStore(database)
        self._notice = notice
        self._handles: dict[str, _Handle] = {}
        self._lock = asyncio.Lock()

    async def start_enabled(self) -> None:
        for spec, enabled in await asyncio.to_thread(self.store.list):
            if not enabled:
                continue
            try:
                await self._connect(spec)
            except Exception as exc:
                await self._notice(spec.id, "mcp.connection.failed", str(exc), True, None)

    async def close(self) -> None:
        async with self._lock:
            handles = list(self._handles.values())
            self._handles.clear()
        for handle in handles:
            await handle.stack.aclose()

    async def add(self, spec: McpServerSpec) -> McpServerView:
        if spec.transport == "stdio" and shutil.which(cast(str, spec.command)) is None:
            raise ValueError(f"MCP command was not found: {spec.command}")
        await asyncio.to_thread(self.store.add, spec)
        await self._notice(
            spec.id, "mcp.server.added", f"MCP {spec.id} added · disabled", False, None
        )
        return self.describe(spec.id)

    async def enable(self, server_id: str) -> McpServerView:
        spec, enabled = await asyncio.to_thread(self.store.get, server_id)
        if enabled and server_id in self._handles:
            return self.describe(server_id)
        try:
            await self._connect(spec)
        except Exception:
            await asyncio.to_thread(self.store.set_enabled, server_id, False)
            raise
        await asyncio.to_thread(self.store.set_enabled, server_id, True)
        await self._notice(server_id, "mcp.server.enabled", f"MCP {server_id} enabled", False, None)
        return self.describe(server_id)

    async def disable(self, server_id: str) -> McpServerView:
        spec, _ = await asyncio.to_thread(self.store.get, server_id)
        await self._disconnect(server_id, require_idle=True)
        await asyncio.to_thread(self.store.set_enabled, server_id, False)
        await self._notice(
            server_id, "mcp.server.disabled", f"MCP {server_id} disabled", False, None
        )
        return self._view(spec, False, None)

    async def remove(self, server_id: str) -> None:
        await asyncio.to_thread(self.store.get, server_id)
        await self._disconnect(server_id, require_idle=True)
        await asyncio.to_thread(self.store.remove, server_id)
        await self._notice(server_id, "mcp.server.removed", f"MCP {server_id} removed", False, None)

    async def inspect(self, server_id: str) -> McpServerView:
        spec, enabled = await asyncio.to_thread(self.store.get, server_id)
        if enabled:
            await self._disconnect(server_id, require_idle=True)
            handle = await self._connect(spec)
            return self._view(spec, True, handle)
        handle = await self._open(spec)
        try:
            return self._view(spec, False, handle)
        finally:
            await handle.stack.aclose()

    def list(self) -> list[McpServerView]:
        return [
            self._view(spec, enabled, self._handles.get(spec.id))
            for spec, enabled in self.store.list()
        ]

    def describe(self, server_id: str) -> McpServerView:
        spec, enabled = self.store.get(server_id)
        return self._view(spec, enabled, self._handles.get(server_id))

    def names(self) -> set[str]:
        return {name for handle in self._handles.values() for name in handle.exposed_tools}

    def definitions(self, allowed: frozenset[str] | None = None) -> list[ToolDefinition]:
        definitions: list[ToolDefinition] = []
        for handle in self._handles.values():
            if handle.status != "ready":
                continue
            for exposed, native in handle.exposed_tools.items():
                if allowed is not None and exposed not in allowed:
                    continue
                tool = handle.tools[native]
                definitions.append(
                    ToolDefinition(
                        name=exposed,
                        description=(
                            f"MCP server {handle.spec.id}: {tool.description or tool.name}"
                        ),
                        input_schema=cast(dict[str, JsonValue], tool.input_schema),
                    )
                )
        return definitions

    def is_tool(self, name: str) -> bool:
        return name.startswith(MCP_TOOL_PREFIX) and any(
            name in handle.exposed_tools for handle in self._handles.values()
        )

    def tool_is_read_only(self, name: str) -> bool:
        handle, native = self._resolve_tool(name)
        annotations = handle.tools[native].annotations
        return bool(
            annotations is not None
            and annotations.read_only_hint is True
            and annotations.destructive_hint is not True
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        context: ToolContext,
        *,
        session_id: str,
    ) -> McpCallOutcome:
        handle, native = self._resolve_tool(name)
        handle.active_calls += 1

        async def progress(progress: float, total: float | None, message: str | None) -> None:
            if message:
                suffix = (
                    f" · {progress:g}/{total:g}"
                    if total is not None
                    else f" · {progress:g}"
                )
                await self._notice(
                    handle.spec.id,
                    "mcp.tool.progress",
                    f"MCP {handle.spec.id} · {message}{suffix}",
                    False,
                    session_id,
                )

        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT_SECONDS):
                result = await handle.client.call_tool(
                    native,
                    cast(dict[str, Any], arguments),
                    read_timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
                    progress_callback=progress,
                )
            return _tool_outcome(result, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            handle.status = "degraded"
            handle.error = str(exc)
            await self._notice(
                handle.spec.id,
                "mcp.tool.failed",
                f"MCP {handle.spec.id} · {exc}",
                True,
                session_id,
            )
            return McpCallOutcome(True, f"MCP tool failed: {exc}", "", {}, False, [])
        finally:
            handle.active_calls -= 1

    async def list_resources(
        self, server_id: str | None, cursor: str | None = None
    ) -> dict[str, JsonValue]:
        if cursor is not None and server_id is None:
            raise ValueError("an MCP resource cursor requires a server id")
        handles = (
            [self._ready_handle(server_id)]
            if server_id is not None
            else [handle for handle in self._handles.values() if handle.status == "ready"]
        )
        servers: list[JsonValue] = []
        for handle in handles:
            resources = await handle.client.list_resources(cursor=cursor)
            templates = await handle.client.list_resource_templates(cursor=cursor)
            servers.append(
                {
                    "server": handle.spec.id,
                    "resources": [
                        _json_value(item.model_dump(mode="json", by_alias=True))
                        for item in resources.resources
                    ],
                    "resource_templates": [
                        _json_value(item.model_dump(mode="json", by_alias=True))
                        for item in templates.resource_templates
                    ],
                    "next_resource_cursor": resources.next_cursor,
                    "next_template_cursor": templates.next_cursor,
                }
            )
        return {"servers": servers}

    async def read_resource(
        self, server_id: str, uri: str, context: ToolContext
    ) -> McpCallOutcome:
        handle = self._ready_handle(server_id)
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT_SECONDS):
                result = await handle.client.read_resource(uri)
            text: list[str] = []
            structured: list[JsonValue] = []
            references: list[str] = []
            for item in result.contents:
                if isinstance(item, TextResourceContents):
                    text.append(item.text)
                    structured.append(
                        {"uri": item.uri, "mime_type": item.mime_type, "kind": "text"}
                    )
                else:
                    raw = base64.b64decode(item.blob, validate=True)
                    digest = context.blobs.put(raw)
                    references.append(digest)
                    structured.append(
                        {
                            "uri": item.uri,
                            "mime_type": item.mime_type,
                            "kind": "blob",
                            "sha256": digest,
                            "bytes": len(raw),
                        }
                    )
            content, truncated, overflow = _bounded_text("\n".join(text), context)
            references.extend(overflow)
            return McpCallOutcome(
                False,
                f"read MCP resource {uri}",
                content,
                {"contents": structured},
                truncated,
                references,
            )
        except Exception as exc:
            await self._notice(
                server_id, "mcp.resource.failed", f"MCP {server_id} · {exc}", True, None
            )
            return McpCallOutcome(True, f"MCP resource read failed: {exc}", "", {}, False, [])

    async def _connect(self, spec: McpServerSpec) -> _Handle:
        async with self._lock:
            existing = self._handles.get(spec.id)
            if existing is not None:
                return existing
            handle = await self._open(spec)
            self._handles[spec.id] = handle
        await self._notice(
            spec.id,
            "mcp.connection.ready",
            (
                f"MCP {spec.id} connected · {len(handle.tools)} tools · "
                f"{len(handle.resources)} resources"
            ),
            False,
            None,
        )
        return handle

    async def _open(self, spec: McpServerSpec) -> _Handle:
        stack = AsyncExitStack()
        await stack.__aenter__()

        async def message_handler(message: Any) -> None:
            if isinstance(message, Exception):
                await self._notice(spec.id, "mcp.connection.failed", str(message), True, None)
                return
            if isinstance(message, LoggingMessageNotification):
                data = message.params.data
                rendered = data if isinstance(data, str) else json.dumps(data, default=str)
                error = message.params.level in {"error", "critical", "alert", "emergency"}
                await self._notice(
                    spec.id,
                    "mcp.server.log",
                    f"MCP {spec.id} · {rendered}",
                    error,
                    None,
                )
                return
            method = str(getattr(message, "method", ""))
            if method.endswith("list_changed"):
                await self._notice(
                    spec.id,
                    "mcp.capabilities.changed",
                    f"MCP {spec.id} capabilities changed · refresh with /mcp",
                    False,
                    None,
                )

        try:
            server: Any
            if spec.transport == "stdio":
                environment = {
                    key: value for key, value in os.environ.items() if key in _BASE_ENVIRONMENT
                }
                for target, source in spec.env.items():
                    if source not in os.environ:
                        raise ValueError(f"required environment variable is missing: {source}")
                    environment[target] = os.environ[source]
                server = StdioServerParameters(
                    command=cast(str, spec.command),
                    args=spec.args,
                    env=environment,
                    cwd=spec.cwd,
                )
            else:
                headers: dict[str, str] = {}
                for target, source in spec.headers.items():
                    if source not in os.environ:
                        raise ValueError(f"required environment variable is missing: {source}")
                    headers[target] = os.environ[source]
                server = _http_transport(cast(str, spec.url), headers)
            client = Client(
                server,
                mode="auto",
                read_timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
                message_handler=message_handler,
                log_level="debug",
            )
            async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
                await stack.enter_async_context(client)
                tools = await _all_tools(client)
                resources = await _all_resources(client)
                templates = await _all_templates(client)
            exposed = _exposed_tool_names(spec.id, [tool.name for tool in tools])
            info = client.server_info
            return _Handle(
                spec=spec,
                stack=stack,
                client=client,
                status="ready",
                protocol_version=client.protocol_version,
                server_name="" if info is None else info.name,
                server_version="" if info is None else info.version,
                tools={tool.name: tool for tool in tools},
                exposed_tools={value: key for key, value in exposed.items()},
                resources=[_resource_view(item, False) for item in resources],
                templates=[_resource_view(item, True) for item in templates],
            )
        except BaseException:
            await stack.aclose()
            raise

    async def _disconnect(self, server_id: str, *, require_idle: bool) -> None:
        async with self._lock:
            handle = self._handles.get(server_id)
            if handle is None:
                return
            if require_idle and handle.active_calls:
                raise RuntimeError(f"MCP server {server_id} has active tool calls")
            self._handles.pop(server_id)
        await handle.stack.aclose()

    def _resolve_tool(self, exposed: str) -> tuple[_Handle, str]:
        for handle in self._handles.values():
            native = handle.exposed_tools.get(exposed)
            if native is not None and handle.status == "ready":
                return handle, native
        raise ValueError(f"unknown or unavailable MCP tool: {exposed}")

    def _ready_handle(self, server_id: str) -> _Handle:
        handle = self._handles.get(server_id)
        if handle is None or handle.status != "ready":
            raise ValueError(f"MCP server is not ready: {server_id}")
        return handle

    def _view(self, spec: McpServerSpec, enabled: bool, handle: _Handle | None) -> McpServerView:
        return McpServerView(
            id=spec.id,
            transport=spec.transport,
            enabled=enabled,
            status="disabled" if handle is None and not enabled else (
                "degraded" if handle is None else handle.status
            ),
            command=spec.command or "",
            args=spec.args,
            cwd=spec.cwd or "",
            url=spec.url or "",
            env=spec.env,
            headers=spec.headers,
            protocol_version="" if handle is None else handle.protocol_version,
            server_name="" if handle is None else handle.server_name,
            server_version="" if handle is None else handle.server_version,
            tools=[]
            if handle is None
            else [
                _tool_view(handle.tools[native], exposed)
                for exposed, native in sorted(handle.exposed_tools.items())
            ],
            resources=[] if handle is None else handle.resources,
            resource_templates=[] if handle is None else handle.templates,
            active_calls=0 if handle is None else handle.active_calls,
            error="" if handle is None else handle.error,
        )


@asynccontextmanager
async def _http_transport(url: str, headers: dict[str, str]) -> AsyncGenerator[Any]:
    async with create_mcp_http_client(headers=headers) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            yield streams


async def _all_tools(client: Client) -> list[Tool]:
    values: list[Tool] = []
    cursor: str | None = None
    while True:
        result = await client.list_tools(cursor=cursor, cache_mode="refresh")
        values.extend(result.tools)
        cursor = result.next_cursor
        if cursor is None:
            return values


async def _all_resources(client: Client) -> list[Resource]:
    values: list[Resource] = []
    cursor: str | None = None
    while True:
        try:
            result = await client.list_resources(cursor=cursor, cache_mode="refresh")
        except Exception as exc:
            if "Method not found" in str(exc):
                return []
            raise
        values.extend(result.resources)
        cursor = result.next_cursor
        if cursor is None:
            return values


async def _all_templates(client: Client) -> list[ResourceTemplate]:
    values: list[ResourceTemplate] = []
    cursor: str | None = None
    while True:
        try:
            result = await client.list_resource_templates(cursor=cursor, cache_mode="refresh")
        except Exception as exc:
            if "Method not found" in str(exc):
                return []
            raise
        values.extend(result.resource_templates)
        cursor = result.next_cursor
        if cursor is None:
            return values


def _resource_view(item: Resource | ResourceTemplate, template: bool) -> McpResourceView:
    uri = item.uri_template if isinstance(item, ResourceTemplate) else item.uri
    return McpResourceView(
        name=item.name,
        uri=uri,
        description=item.description or "",
        mime_type=item.mime_type or "",
        template=template,
    )


def _tool_view(tool: Tool, exposed_name: str) -> McpToolView:
    annotations = tool.annotations
    return McpToolView(
        name=tool.name,
        exposed_name=exposed_name,
        description=tool.description or "",
        read_only=bool(
            annotations is not None
            and annotations.read_only_hint is True
            and annotations.destructive_hint is not True
        ),
        destructive=bool(annotations is not None and annotations.destructive_hint is True),
    )


def _exposed_tool_names(server_id: str, names: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    used: set[str] = set()
    server = server_id.replace("-", "_")
    for native in names:
        component = _SAFE_TOOL_COMPONENT.sub("_", native).strip("_") or "tool"
        base = f"{MCP_TOOL_PREFIX}{server}__{component}"
        exposed = base[:64]
        if exposed in used or len(base) > 64:
            suffix = hashlib.sha256(native.encode()).hexdigest()[:8]
            exposed = f"{base[:55]}_{suffix}"
        if exposed in used:
            raise ValueError(f"MCP tool name collision on server {server_id}: {native}")
        used.add(exposed)
        result[native] = exposed
    return result


def _tool_outcome(result: CallToolResult, context: ToolContext) -> McpCallOutcome:
    text: list[str] = []
    blocks: list[JsonValue] = []
    references: list[str] = []
    for item in result.content:
        if isinstance(item, TextContent):
            text.append(item.text)
        elif isinstance(item, (ImageContent, AudioContent)):
            raw = base64.b64decode(item.data, validate=True)
            digest = context.blobs.put(raw)
            references.append(digest)
            blocks.append(
                {
                    "kind": "image" if isinstance(item, ImageContent) else "audio",
                    "mime_type": item.mime_type,
                    "sha256": digest,
                    "bytes": len(raw),
                }
            )
        elif isinstance(item, ResourceLink):
            blocks.append(_json_value(item.model_dump(mode="json")))
        else:
            resource = item.resource
            if isinstance(resource, TextResourceContents):
                text.append(resource.text)
            else:
                raw = base64.b64decode(resource.blob, validate=True)
                digest = context.blobs.put(raw)
                references.append(digest)
                blocks.append({"kind": "blob", "sha256": digest, "bytes": len(raw)})
    structured: dict[str, JsonValue] = {"content_blocks": blocks} if blocks else {}
    if result.structured_content is not None:
        value = _json_value(result.structured_content)
        if isinstance(value, dict):
            structured.update(value)
        else:
            structured["result"] = value
    content, truncated, overflow = _bounded_text("\n".join(text), context)
    references.extend(overflow)
    failed = bool(result.is_error)
    return McpCallOutcome(
        failed,
        (text[0][:160] if failed and text else "MCP tool completed"),
        content,
        structured,
        truncated,
        references,
    )


def _bounded_text(content: str, context: ToolContext) -> tuple[str, bool, list[str]]:
    if len(content) <= context.config.model_result_char_limit:
        return content, False, []
    digest = context.blobs.put(content.encode())
    limit = context.config.model_result_char_limit
    return content[:limit] + "\n[output truncated]", True, [digest]


def _json_value(value: Any) -> JsonValue:
    return cast(JsonValue, json.loads(json.dumps(value, default=str)))
