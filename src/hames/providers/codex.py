"""Codex subscription provider backed by the local Codex app-server protocol."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import cast

from hames.providers.base import (
    JSON_OBJECT,
    JsonValue,
    ModelRequest,
    ProviderError,
    ProviderMessage,
    ProviderModel,
    StreamEvent,
    StreamEventKind,
    ToolCallDelta,
    Usage,
)

# asyncio StreamReader defaults to 64KiB. A write_file tool call is one JSONL line.
JSONL_LINE_LIMIT = 16 * 1024 * 1024
CODEX_DEFAULT_CONTEXT_TOKENS = 272_000


class _CodexConnection:
    def __init__(self, command: Sequence[str], timeout_seconds: float) -> None:
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self.process: asyncio.subprocess.Process | None = None
        self.next_id = 1
        self.pending_messages: list[dict[str, JsonValue]] = []

    async def open(self) -> None:
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                "app-server",
                "--listen",
                "stdio://",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=JSONL_LINE_LIMIT,
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                "provider_not_configured",
                f"Codex executable was not found: {self.command[0]}",
            ) from exc
        await self.request(
            "initialize",
            {
                "clientInfo": {"name": "hames", "title": "Hames", "version": "0.0.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.notify("initialized", {})

    async def close(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        await self._write({"method": method, "params": dict(params)})

    async def request(
        self, method: str, params: Mapping[str, object] | None
    ) -> dict[str, JsonValue]:
        request_id = self.next_id
        self.next_id += 1
        request: dict[str, object] = {"id": request_id, "method": method}
        if params is not None:
            request["params"] = dict(params)
        await self._write(request)
        while True:
            message = await self.read()
            if message.get("id") != request_id:
                self.pending_messages.append(message)
                continue
            error = message.get("error")
            if isinstance(error, dict):
                code = str(error.get("code", "codex_protocol_error"))
                detail = str(error.get("message", "Codex app-server request failed"))
                raise ProviderError(code, detail)
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise ProviderError(
                    "malformed_provider_response", f"Codex {method} result was not an object"
                )
            return cast(dict[str, JsonValue], result)

    async def next_message(self) -> dict[str, JsonValue]:
        if self.pending_messages:
            return self.pending_messages.pop(0)
        return await self.read()

    async def read(self) -> dict[str, JsonValue]:
        process = self.process
        if process is None or process.stdout is None:
            raise ProviderError("provider_protocol_error", "Codex app-server is not running")
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            raise ProviderError(
                "provider_timeout", "Codex app-server timed out", retryable=True
            ) from exc
        except asyncio.LimitOverrunError as exc:
            raise ProviderError(
                "provider_protocol_error",
                "Codex JSONL message exceeded the stdio line limit",
            ) from exc
        if not line:
            raise ProviderError(
                "provider_transport_error",
                "Codex app-server exited before completing the request",
                retryable=True,
            )
        try:
            return JSON_OBJECT.validate_json(line)
        except ValueError as exc:
            raise ProviderError(
                "malformed_provider_event", "Codex app-server emitted invalid JSON"
            ) from exc

    async def respond_error(self, request_id: JsonValue, message: str) -> None:
        await self._write(
            {
                "id": request_id,
                "error": {"code": -32601, "message": message},
            }
        )

    async def respond_tool_result(self, request_id: JsonValue, text: str, *, success: bool) -> None:
        await self._write(
            {
                "id": request_id,
                "result": {
                    "contentItems": [{"type": "inputText", "text": text}],
                    "success": success,
                },
            }
        )

    async def _write(self, value: Mapping[str, object]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise ProviderError("provider_protocol_error", "Codex app-server is not running")
        process.stdin.write(json.dumps(dict(value), separators=(",", ":")).encode() + b"\n")
        try:
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ProviderError(
                "provider_transport_error", "Codex app-server closed its input", retryable=True
            ) from exc


class CodexProvider:
    adapter = "codex"

    def __init__(
        self,
        *,
        profile_id: str = "codex",
        command: Sequence[str] = ("codex",),
        timeout_seconds: float = 120.0,
        default_model: str = "",
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.base_url = "app-server://codex"
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self.default_model = default_model
        self.environ = os.environ if environ is None else environ
        self._rate_limits_cache: dict[str, JsonValue] | None = None
        self._rate_limits_cached_at = 0.0
        self._rate_limits_lock = asyncio.Lock()

    def cached_account_rate_limits(self) -> dict[str, JsonValue] | None:
        if self._rate_limits_cache is None:
            return None
        return dict(self._rate_limits_cache)

    async def aclose(self) -> None:
        return None

    def _connection(self) -> _CodexConnection:
        configured = self.environ.get("HAMES_CODEX_BIN", "").strip()
        command = (configured,) if configured else self.command
        return _CodexConnection(command, self.timeout_seconds)

    async def list_models(self) -> list[ProviderModel]:
        connection = self._connection()
        try:
            await connection.open()
            account = await connection.request("account/read", {"refreshToken": False})
            if not isinstance(account.get("account"), dict):
                raise ProviderError(
                    "provider_not_configured",
                    "Codex is not signed in; run `codex login` or `hames setup codex`",
                )
            result: list[ProviderModel] = []
            cursor: str | None = None
            while True:
                page = await connection.request(
                    "model/list", {"cursor": cursor, "limit": 100, "includeHidden": False}
                )
                raw_models = page.get("data", [])
                if not isinstance(raw_models, list):
                    raise ProviderError(
                        "malformed_provider_response", "Codex model list was not an array"
                    )
                for raw_value in raw_models:
                    if not isinstance(raw_value, dict):
                        continue
                    raw = cast(dict[str, JsonValue], raw_value)
                    if raw.get("hidden") is True:
                        continue
                    model_id = str(raw.get("model") or raw.get("id") or "")
                    if not model_id:
                        continue
                    efforts = _codex_efforts(raw.get("supportedReasoningEfforts"))
                    modalities = _string_list(raw.get("inputModalities")) or ["text"]
                    result.append(
                        ProviderModel(
                            id=model_id,
                            provider=self.profile_id,
                            status="available",
                            context_length=_codex_context_length(raw),
                            input_modalities=modalities,
                            output_modalities=["text"],
                            reasoning_supported=bool(efforts),
                            reasoning_efforts=efforts,
                        )
                    )
                raw_cursor = page.get("nextCursor")
                cursor = raw_cursor if isinstance(raw_cursor, str) and raw_cursor else None
                if cursor is None:
                    break
            if self.default_model and all(model.id != self.default_model for model in result):
                result.append(
                    ProviderModel(
                        id=self.default_model,
                        provider=self.profile_id,
                        status="configured",
                        input_modalities=["text"],
                        output_modalities=["text"],
                        reasoning_supported=None,
                    )
                )
            return result
        finally:
            await connection.close()

    async def account_rate_limits(self) -> dict[str, JsonValue]:
        async with self._rate_limits_lock:
            if (
                self._rate_limits_cache is not None
                and time.monotonic() - self._rate_limits_cached_at < 60
            ):
                return dict(self._rate_limits_cache)
            connection = self._connection()
            try:
                await connection.open()
                response = await connection.request("account/rateLimits/read", None)
            finally:
                await connection.close()
            normalized = _normalize_rate_limits(response)
            self._rate_limits_cache = normalized
            self._rate_limits_cached_at = time.monotonic()
            return dict(normalized)

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        connection = self._connection()
        latest_usage: Usage | None = None
        try:
            await connection.open()
            dynamic_tools = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                }
                for tool in request.tools
            ]
            thread = await connection.request(
                "thread/start",
                {
                    "ephemeral": True,
                    "cwd": _request_workspace(request),
                    "model": request.model,
                    "approvalPolicy": "never",
                    # Codex is hosted behind Hames' tool and policy boundary. Advertising the
                    # native read-only sandbox here caused the model to treat Hames writes as
                    # impossible even though dynamic tools remained available.
                    "sandbox": "danger-full-access",
                    "baseInstructions": request.system,
                    "developerInstructions": _codex_instructions(request),
                    "config": _codex_isolation_config(),
                    "environments": [],
                    "dynamicTools": dynamic_tools,
                },
            )
            thread_object = thread.get("thread", {})
            thread_id = str(thread_object.get("id", "")) if isinstance(thread_object, dict) else ""
            if not thread_id:
                raise ProviderError(
                    "malformed_provider_response", "Codex thread/start omitted the thread id"
                )
            turn = await connection.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": _codex_input(request.messages)}],
                    "model": request.model,
                    "effort": _codex_wire_effort(request.reasoning_effort),
                    "approvalPolicy": "never",
                    # Hames is the external sandbox. Codex native tools are disabled below;
                    # every model-visible side effect must arrive as an item/tool/call request.
                    "sandboxPolicy": {
                        "type": "externalSandbox",
                        "networkAccess": "restricted",
                    },
                },
            )
            turn_object = turn.get("turn", {})
            turn_id = str(turn_object.get("id", "")) if isinstance(turn_object, dict) else ""
            yield StreamEvent(
                kind=StreamEventKind.STARTED,
                provider_request_id=turn_id or thread_id,
            )

            tool_index = 0
            while True:
                message = await connection.next_message()
                method = str(message.get("method", ""))
                params_value = message.get("params", {})
                params = (
                    cast(dict[str, JsonValue], params_value)
                    if isinstance(params_value, dict)
                    else {}
                )
                if "id" in message:
                    if method != "item/tool/call":
                        await connection.respond_error(
                            message.get("id"), "Hames only accepts Codex dynamic tool calls"
                        )
                        raise ProviderError(
                            "provider_protocol_error",
                            f"Codex requested unsupported native action: {method or 'unknown'}",
                        )
                    call_id = str(params.get("callId", ""))
                    tool_name = str(params.get("tool", ""))
                    arguments = params.get("arguments", {})
                    if not call_id or not tool_name or not isinstance(arguments, dict):
                        raise ProviderError(
                            "malformed_provider_event", "Codex emitted an invalid dynamic tool call"
                        )
                    yield StreamEvent(
                        kind=StreamEventKind.TOOL_CALL_DELTA,
                        tool_call=ToolCallDelta(
                            index=tool_index,
                            provider_call_id=call_id,
                            name=tool_name,
                            arguments_delta=json.dumps(arguments, separators=(",", ":")),
                        ),
                    )
                    tool_index += 1
                    handler = request.tool_handler
                    if handler is None:
                        yield StreamEvent(
                            kind=StreamEventKind.COMPLETED,
                            finish_reason="tool_calls",
                            provider_request_id=turn_id or thread_id,
                        )
                        return
                    result = await handler(tool_name, arguments, call_id)
                    text = result.for_model() if hasattr(result, "for_model") else str(result)
                    success = getattr(result, "status", "completed") == "completed"
                    await connection.respond_tool_result(message.get("id"), text, success=success)
                    continue
                if method in {
                    "item/reasoning/summaryTextDelta",
                    "item/reasoning/textDelta",
                }:
                    yield StreamEvent(
                        kind=StreamEventKind.REASONING_DELTA,
                        text=str(params.get("delta", "")),
                    )
                elif method == "item/agentMessage/delta":
                    yield StreamEvent(
                        kind=StreamEventKind.TEXT_DELTA,
                        text=str(params.get("delta", "")),
                    )
                elif method == "thread/tokenUsage/updated":
                    latest_usage = _codex_usage(params.get("tokenUsage"))
                elif method == "turn/completed":
                    raw_turn = params.get("turn", {})
                    if not isinstance(raw_turn, dict):
                        raise ProviderError(
                            "malformed_provider_event", "Codex completion omitted the turn"
                        )
                    status = str(raw_turn.get("status", ""))
                    if status != "completed":
                        error = raw_turn.get("error", {})
                        detail = (
                            str(error.get("message", f"Codex turn {status}"))
                            if isinstance(error, dict)
                            else f"Codex turn {status}"
                        )
                        raise ProviderError("provider_response_failed", detail)
                    if latest_usage is not None:
                        yield StreamEvent(kind=StreamEventKind.USAGE, usage=latest_usage)
                    yield StreamEvent(
                        kind=StreamEventKind.COMPLETED,
                        finish_reason="stop",
                        provider_request_id=turn_id or thread_id,
                    )
                    return
        finally:
            await connection.close()


def _request_workspace(request: ModelRequest) -> str:
    value = request.metadata.get("workspace_path")
    return value if isinstance(value, str) and value else os.getcwd()


def _codex_isolation_config() -> dict[str, JsonValue]:
    """Remove Codex-owned agent state and tools from an embedded provider thread."""

    return {
        "features": {
            "memories": False,
            "external_agent_memory_import": False,
            "multi_agent": False,
            "multi_agent_v2": False,
            "shell_tool": False,
        },
        "memories": {"generate_memories": False, "use_memories": False},
        "mcp_servers": {},
        "plugins": {},
    }


def _codex_instructions(request: ModelRequest) -> str:
    workspace = _request_workspace(request)
    mode = request.metadata.get("interaction_mode")
    rendered_mode = mode if isinstance(mode, str) and mode else "auto"
    return (
        "You are the model provider inside Hames. Hames owns the conversation, permissions, "
        "tools, and every side effect. Never use Codex native shell, file, web, or delegation "
        "tools. Use only the dynamic tools supplied by Hames. Do not load or mention Codex "
        "memories, Skills, sessions, project rules, or configuration. The Hames workspace is "
        f"{workspace!r} and the interaction mode is {rendered_mode!r}. Hames dynamic tools may "
        "access the project, disposable scratch, and user-requested paths below their home, "
        "subject to Hames policy and approvals. Never infer that Hames is read-only from Codex "
        "sandbox metadata. Attempt the supplied Hames tool and treat its structured result as "
        "the only authority. Treat the serialized transcript in the user input as authoritative "
        "conversation history."
    )


def _normalize_rate_limits(response: dict[str, JsonValue]) -> dict[str, JsonValue]:
    raw_snapshot = response.get("rateLimits")
    snapshot = cast(dict[str, JsonValue], raw_snapshot) if isinstance(raw_snapshot, dict) else {}
    raw_by_limit = response.get("rateLimitsByLimitId")
    if isinstance(raw_by_limit, dict) and isinstance(raw_by_limit.get("codex"), dict):
        snapshot = cast(dict[str, JsonValue], raw_by_limit["codex"])
    normalized: dict[str, JsonValue] = {
        "plan_type": snapshot.get("planType"),
        "limit_id": snapshot.get("limitId"),
        "observed_at": time.time(),
    }
    for fallback, key in (("primary", "primary"), ("secondary", "secondary")):
        raw_window = snapshot.get(key)
        if not isinstance(raw_window, dict):
            continue
        minutes = raw_window.get("windowDurationMins")
        label = (
            "sliding_window_5h"
            if minutes == 300
            else "weekly_window"
            if minutes == 10_080
            else fallback
        )
        raw_used = raw_window.get("usedPercent")
        used = max(0, min(100, raw_used if isinstance(raw_used, int) else 0))
        normalized[label] = {
            "used": used,
            "remaining": 100 - used,
            "reset_at": raw_window.get("resetsAt"),
            "window_minutes": minutes,
        }
    return normalized


def _codex_input(messages: list[ProviderMessage]) -> str:
    sections = ["HAMES CONVERSATION TRANSCRIPT"]
    for message in messages:
        if message.role == "tool":
            sections.append(
                f"TOOL RESULT {message.tool_name or ''} ({message.tool_call_id or ''})\n"
                f"{message.content}"
            )
            continue
        sections.append(f"{message.role.upper()}\n{message.content}")
        for call in message.tool_calls:
            sections.append(
                f"TOOL CALL {call.name} ({call.id})\n"
                + json.dumps(call.arguments, separators=(",", ":"), sort_keys=True)
            )
    sections.append("Continue from the final transcript entry.")
    return "\n\n".join(sections)


def _codex_wire_effort(value: str) -> str | None:
    effort = value.strip()
    if not effort or effort in {"on", "default"}:
        return None
    if effort in {"off", "disabled"}:
        return "none"
    return effort


def _codex_context_length(raw: Mapping[str, JsonValue]) -> int:
    for key in ("maxContextWindow", "max_context_window", "contextWindow", "context_length"):
        value = raw.get(key)
        if isinstance(value, int) and value >= 8_192:
            return value
        if isinstance(value, float) and value >= 8_192:
            return int(value)
    return CODEX_DEFAULT_CONTEXT_TOKENS


def _codex_efforts(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        return []
    efforts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        effort = item.get("reasoningEffort")
        if isinstance(effort, str) and effort and effort not in efforts:
            efforts.append(effort)
    return efforts


def _string_list(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _codex_usage(value: JsonValue) -> Usage | None:
    if not isinstance(value, dict):
        return None
    last = value.get("last", {})
    if not isinstance(last, dict):
        return None
    return Usage(
        input_tokens=_integer(last.get("inputTokens")),
        output_tokens=_integer(last.get("outputTokens")),
        cached_input_tokens=_integer(last.get("cachedInputTokens")),
        reasoning_tokens=_integer(last.get("reasoningOutputTokens")),
    )


def _integer(value: JsonValue) -> int:
    return int(value) if isinstance(value, int | float) else 0
