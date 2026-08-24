"""Host-side capability broker for plugin workers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from hames.ledger import Event, Session, new_id
from hames.policy import PolicyDecisionKind, PolicyGate, approval_request_hash
from hames.providers.base import JSON_OBJECT, JsonValue
from hames.rules import PolicyRule
from hames.tools import (
    ListDirArguments,
    ListDirTool,
    ReadFileArguments,
    ReadFileTool,
    ShellArguments,
    ShellTool,
    ToolArguments,
    ToolBase,
    ToolContext,
    WriteFileArguments,
    WriteFileTool,
)

BROKER_TO_PERMISSION = {
    "project.read": "broker:project_read",
    "project.list": "broker:project_read",
    "project.write": "broker:project_write",
    "process.run_scoped": "broker:process_run_scoped",
    "network.request": "broker:network_request",
}

Append = Callable[..., Awaitable[Event]]


class CapabilityBroker:
    def __init__(
        self,
        *,
        plugin_id: str,
        permissions: frozenset[str],
        policy: PolicyGate,
        session: Session,
        context: ToolContext,
        allowed_tools: frozenset[str],
        append: Append,
        run_id: str | None,
        declarative_rules: Sequence[PolicyRule] = (),
    ) -> None:
        self.plugin_id = plugin_id
        self.permissions = permissions
        self.policy = policy
        self.session = session
        self.context = context
        self.allowed_tools = allowed_tools
        self.declarative_rules = declarative_rules
        self._append = append
        self.run_id = run_id

    async def call(self, method: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        permission = BROKER_TO_PERMISSION.get(method)
        if permission is None:
            raise ValueError(f"unknown broker method: {method}")
        await self._broker_event(method, "requested")
        try:
            if permission not in self.permissions:
                raise PermissionError(f"plugin {self.plugin_id} lacks {permission}")
            result = await self._execute(method, arguments)
        except Exception as exc:
            await self._broker_event(method, "denied", reason=str(exc))
            raise
        await self._broker_event(method, "completed")
        return result

    async def _execute(self, method: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if method == "network.request":
            raise PermissionError("network.request is denied by default")
        payload: dict[str, JsonValue] = {"workspace": "project", **arguments}
        tool_name: str
        tool_args: ToolArguments
        tool: ToolBase
        if method == "project.read":
            tool_name = "read_file"
            tool_args = ReadFileArguments.model_validate(payload)
            tool = ReadFileTool()
        elif method == "project.list":
            tool_name = "list_dir"
            tool_args = ListDirArguments.model_validate(payload)
            tool = ListDirTool()
        elif method == "project.write":
            tool_name = "write_file"
            tool_args = WriteFileArguments.model_validate(payload)
            tool = WriteFileTool()
        elif method == "process.run_scoped":
            tool_name = "shell"
            tool_args = ShellArguments.model_validate(payload)
            tool = ShellTool()
        else:
            raise ValueError(f"unknown broker method: {method}")
        await self._require(tool_name, tool_args)
        result = await tool.execute(self.context, tool_args)
        return JSON_OBJECT.validate_python(result.model_dump(mode="json"))

    async def _require(self, tool_name: str, arguments: ToolArguments) -> None:
        dumped = JSON_OBJECT.validate_python(arguments.model_dump(mode="json"))
        request_hash = approval_request_hash(
            tool_name=tool_name,
            arguments=dumped,
            session_id=self.session.id,
            run_id=self.run_id or "",
            agent_id=self.session.agent_id,
            working_directory=self.session.working_directory,
        )
        tool_call_id = new_id()
        requested = await self._append(
            session_id=self.session.id,
            run_id=self.run_id,
            agent_id=self.session.agent_id,
            event_type="policy.requested",
            payload={
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "request_hash": request_hash,
            },
            correlation_id=self.run_id,
        )
        decision = self.policy.decide(
            tool_name,
            arguments,
            self.context,
            allowed_tools=self.allowed_tools,
            declarative_rules=self.declarative_rules,
        )
        await self._append(
            session_id=self.session.id,
            run_id=self.run_id,
            agent_id=self.session.agent_id,
            event_type="policy.decided",
            payload={
                "tool_call_id": tool_call_id,
                "decision": decision.decision.value,
                "reason": decision.reason,
                "risk": decision.risk,
            },
            causation_id=requested.id,
            correlation_id=self.run_id,
        )
        if decision.decision is not PolicyDecisionKind.ALLOW:
            raise PermissionError(decision.reason)

    async def _broker_event(self, method: str, status: str, *, reason: str = "") -> None:
        event_type = (
            "plugin.broker.requested" if status == "requested" else "plugin.broker.completed"
        )
        await self._append(
            session_id=self.session.id,
            run_id=self.run_id,
            agent_id=self.session.agent_id,
            event_type=event_type,
            payload={
                "plugin_id": self.plugin_id,
                "method": method,
                "status": status,
                "reason": reason,
            },
            correlation_id=self.run_id,
        )
