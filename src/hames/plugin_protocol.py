"""Newline-delimited JSON RPC between the controller and a plugin worker."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hames.providers.base import JSON_OBJECT, JsonValue

JSONL_LINE_LIMIT = 16 * 1024 * 1024

BrokerHandler = Callable[[str, dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]]


class RpcModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PluginToolSpec(RpcModel):
    name: str
    description: str
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)


class InitializeResult(RpcModel):
    plugin_id: str
    version: str
    api_version: int
    tools: list[PluginToolSpec] = Field(default_factory=lambda: list[PluginToolSpec]())
    context_sources: list[str] = Field(default_factory=lambda: list[str]())
    event_filters: list[str] = Field(default_factory=lambda: list[str]())


class ToolExecuteResult(RpcModel):
    content: str = ""
    summary: str = ""
    structured: dict[str, JsonValue] = Field(default_factory=dict)


class ContextCollectResult(RpcModel):
    sources: list[dict[str, JsonValue]] = Field(
        default_factory=lambda: list[dict[str, JsonValue]]()
    )


class PluginProtocolError(RuntimeError):
    pass


class PluginWorker:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        timeout_seconds: float,
        on_broker: BrokerHandler | None = None,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise PluginProtocolError("plugin worker is missing stdio")
        self._process = process
        self._stdin = process.stdin
        self._stdout = process.stdout
        self._timeout = timeout_seconds
        self._on_broker = on_broker
        self._pending: dict[str, asyncio.Future[dict[str, JsonValue]]] = {}
        self._next_id = 1
        self._reader = asyncio.create_task(self._read_loop(), name="hames-plugin-reader")

    @property
    def pid(self) -> int | None:
        return self._process.pid

    async def request(
        self, method: str, params: dict[str, JsonValue] | None = None
    ) -> dict[str, JsonValue]:
        message_id = str(self._next_id)
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, JsonValue]] = loop.create_future()
        self._pending[message_id] = future
        payload = {"id": message_id, "method": method, "params": params or {}}
        self._stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        await self._stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError as exc:
            future.cancel()
            self._pending.pop(message_id, None)
            raise PluginProtocolError(f"plugin worker timed out on {method}") from exc

    async def initialize(self, plugin_id: str, version: str) -> InitializeResult:
        raw = await self.request(
            "initialize",
            {"plugin_id": plugin_id, "version": version, "api_version": 1},
        )
        return InitializeResult.model_validate(raw)

    async def execute_tool(self, name: str, arguments: dict[str, JsonValue]) -> ToolExecuteResult:
        raw = await self.request("tool.execute", {"name": name, "arguments": arguments})
        return ToolExecuteResult.model_validate(raw)

    async def collect_context(self, query: str) -> ContextCollectResult:
        raw = await self.request("context.collect", {"query": query})
        return ContextCollectResult.model_validate(raw)

    async def deliver_event(self, event: dict[str, JsonValue]) -> None:
        await self.request("event.deliver", {"event": event})

    async def shutdown(self) -> None:
        try:
            if self._process.returncode is None:
                try:
                    if not self._reader.done():
                        await self.request("shutdown", {})
                except (PluginProtocolError, ConnectionError):
                    pass
                try:
                    self._process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=2)
                except TimeoutError:
                    try:
                        self._process.kill()
                    except ProcessLookupError:
                        pass
                    await self._process.wait()
        finally:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)

    async def _read_loop(self) -> None:
        try:
            while True:
                try:
                    line = await self._stdout.readline()
                except asyncio.LimitOverrunError as exc:
                    raise PluginProtocolError(
                        "plugin JSONL message exceeded the stdio line limit"
                    ) from exc
                if not line:
                    break
                try:
                    message = JSON_OBJECT.validate_python(json.loads(line.decode()))
                except (json.JSONDecodeError, ValueError):
                    continue
                if "method" in message and message.get("method") == "broker.call":
                    await self._handle_broker(message)
                    continue
                message_id = str(message.get("id", ""))
                future = self._pending.pop(message_id, None)
                if future is None or future.done():
                    continue
                if "error" in message:
                    future.set_exception(
                        PluginProtocolError(str(message.get("error") or "plugin error"))
                    )
                    continue
                result = message.get("result", {})
                if not isinstance(result, dict):
                    future.set_exception(PluginProtocolError("plugin result must be an object"))
                    continue
                future.set_result(JSON_OBJECT.validate_python(result))
        except asyncio.CancelledError:
            return
        except PluginProtocolError as exc:
            error = exc
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()
            return
        finally:
            error = PluginProtocolError("plugin worker closed")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()

    async def _handle_broker(self, message: dict[str, JsonValue]) -> None:
        message_id = str(message.get("id", ""))
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {}
        method = str(params.get("method", ""))
        arguments_raw = params.get("arguments", {})
        arguments = (
            JSON_OBJECT.validate_python(arguments_raw) if isinstance(arguments_raw, dict) else {}
        )
        try:
            if self._on_broker is None:
                raise PluginProtocolError("capability broker is unavailable")
            result = await self._on_broker(method, arguments)
            reply: dict[str, Any] = {"id": message_id, "result": result}
        except Exception as exc:
            reply = {"id": message_id, "error": str(exc)}
        self._stdin.write((json.dumps(reply, separators=(",", ":")) + "\n").encode())
        await self._stdin.drain()


async def spawn_worker(
    command: list[str],
    *,
    timeout_seconds: float,
    on_broker: BrokerHandler | None = None,
) -> PluginWorker:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=JSONL_LINE_LIMIT,
    )
    return PluginWorker(process, timeout_seconds=timeout_seconds, on_broker=on_broker)
