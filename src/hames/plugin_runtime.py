"""Install, enable, and run isolated plugin workers."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.context import PluginContextItem
from hames.ledger import Event, Ledger, Session, new_id
from hames.paths import HamesPaths
from hames.plugin_broker import Append, CapabilityBroker
from hames.plugin_protocol import PluginProtocolError, PluginToolSpec, PluginWorker, spawn_worker
from hames.plugin_sandbox import PluginSandboxError, bwrap_available, worker_command
from hames.plugins import (
    TOOL_SUFFIX,
    InspectedPlugin,
    PluginManifest,
    PluginProposalRecord,
    PluginStore,
    PluginVersionRecord,
    inspect_package,
    tool_id,
)
from hames.policy import PolicyGate
from hames.providers import ToolDefinition
from hames.providers.base import JSON_OBJECT, JsonValue
from hames.tools import ToolArguments, ToolContext, ToolResult

PLUGIN_TOOL_NAME = re.compile(rf"^{TOOL_SUFFIX}$")

_PROPOSAL_WORKER = """\
import json
import sys


def send(message):
    sys.stdout.write(json.dumps(message, separators=(',', ':')) + '\\n')
    sys.stdout.flush()


def main():
    for raw in sys.stdin:
        message = json.loads(raw)
        method = str(message.get('method', ''))
        message_id = message.get('id')
        if method == 'initialize':
            send({
                'id': message_id,
                'result': {
                    'plugin_id': message.get('params', {}).get('plugin_id', 'proposal'),
                    'version': '0.1.0',
                    'api_version': 1,
                    'tools': [{
                        'name': 'describe',
                        'description': 'Summarize the missing capability this proposal covers',
                        'input_schema': {'type': 'object'},
                    }],
                    'context_sources': [],
                    'event_filters': [],
                },
            })
        elif method == 'tool.execute':
            send({
                'id': message_id,
                'result': {
                    'summary': 'proposal is not installed',
                    'content': 'This worker belongs to an uninstalled proposal.',
                },
            })
        elif method == 'shutdown':
            send({'id': message_id, 'result': {}})
            return
        else:
            send({'id': message_id, 'error': f'unknown method: {method}'})


if __name__ == '__main__':
    main()
"""


class PluginToolArguments(ToolArguments):
    model_config = ConfigDict(extra="allow")


class PluginView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    enabled: bool
    running: bool = False
    version: str = ""
    fingerprint: str = ""
    permissions: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    warning: str = ""


class PluginInspectView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    fingerprint: str
    permissions: list[str]
    capabilities: list[str]
    entrypoint: str
    files: list[str]


class PluginProposalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    plugin_id: str = ""
    scar_id: str = ""
    status: str
    package_path: str
    permissions: list[str] = Field(default_factory=list)
    created_at: str


@dataclass
class RunningPlugin:
    version: PluginVersionRecord
    worker: PluginWorker
    tools: list[PluginToolSpec]
    context_sources: list[str] = field(default_factory=list[str])
    event_filters: list[str] = field(default_factory=list[str])
    warning: str = ""
    broker: CapabilityBroker | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def on_broker(self, method: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if self.broker is None:
            raise PermissionError("capability broker is not attached")
        return await self.broker.call(method, arguments)


class PluginManager:
    def __init__(
        self,
        *,
        paths: HamesPaths,
        ledger: Ledger,
        config: HamesConfig,
        events: EventBroker,
        policy: PolicyGate,
    ) -> None:
        self.paths = paths
        self.ledger = ledger
        self.config = config
        self.events = events
        self.policy = policy
        self.store = PluginStore(paths, ledger)
        self._handles: dict[str, RunningPlugin] = {}

    def inspect(self, path: Path) -> PluginInspectView:
        return _inspect_view(inspect_package(path))

    def list_plugins(self) -> list[PluginView]:
        return [self.describe(plugin.id) for plugin in self.store.list_plugins()]

    def list_proposals(self) -> list[PluginProposalView]:
        return [_proposal_view(item) for item in self.store.list_proposals()]

    def describe_proposal(self, proposal_id: str) -> PluginProposalView:
        return _proposal_view(self.store.get_proposal(proposal_id))

    def describe(self, plugin_id: str) -> PluginView:
        plugin = self.store.get(plugin_id)
        version = self.store.active_version(plugin_id)
        handle = self._handles.get(plugin_id)
        tools = (
            [tool_id(plugin_id, spec.name) for spec in handle.tools] if handle is not None else []
        )
        return PluginView(
            id=plugin.id,
            name=plugin.name,
            enabled=plugin.enabled,
            running=handle is not None,
            version="" if version is None else version.version,
            fingerprint="" if version is None else version.fingerprint,
            permissions=[] if version is None else list(version.permissions),
            tools=tools,
            warning="" if handle is None else handle.warning,
        )

    def names(self) -> set[str]:
        if not self.config.plugins.enabled:
            return set()
        names: set[str] = set()
        for plugin_id, handle in self._handles.items():
            names.update(tool_id(plugin_id, spec.name) for spec in handle.tools)
        return names

    def definitions(self, allowed: frozenset[str] | None = None) -> list[ToolDefinition]:
        values: list[ToolDefinition] = []
        for plugin_id, handle in self._handles.items():
            for spec in handle.tools:
                name = tool_id(plugin_id, spec.name)
                if allowed is not None and name not in allowed:
                    continue
                values.append(
                    ToolDefinition(
                        name=name,
                        description=spec.description,
                        input_schema=spec.input_schema,
                    )
                )
        return values

    def control_session(self) -> Session:
        for session in self.ledger.list_sessions():
            if session.status == "open" and session.title == "plugin-control":
                return session
        return self.ledger.create_session(
            working_directory=self.paths.root,
            agent_id="default",
            provider="hames",
            model="control",
            title="plugin-control",
        )

    async def install(self, path: Path, *, session: Session | None = None) -> PluginView:
        selected = session or self.control_session()

        def _install() -> PluginVersionRecord:
            resolved = path.expanduser().resolve(strict=True)
            version = self.store.install(
                resolved, session_id=selected.id, agent_id=selected.agent_id
            )
            for proposal in self.store.list_proposals():
                if proposal.status != "proposed":
                    continue
                if Path(proposal.package_path).expanduser().resolve() == resolved:
                    self.store.mark_proposal_status(proposal.id, "installed")
            return version

        version = await asyncio.to_thread(_install)
        await self._publish_latest(selected.id)
        return self.describe(version.plugin_id)

    async def propose_from_scar(
        self,
        *,
        session: Session,
        scar_id: str,
        title: str,
        description: str,
        expected_behavior: str,
        evidence_event_ids: list[str],
    ) -> PluginProposalView:
        def _write() -> PluginProposalRecord:
            proposal_id = new_id()
            plugin_id = f"cap-{proposal_id.replace('-', '')[:8]}"
            dest = self.paths.plugins / "proposals" / proposal_id
            dest.mkdir(mode=0o700, parents=True, exist_ok=False)
            dest.chmod(0o700)
            name = f"Capability: {title[:48]}"
            (dest / "plugin.toml").write_text(
                "\n".join(
                    [
                        f"id = {json.dumps(plugin_id)}",
                        f"name = {json.dumps(name)}",
                        'version = "0.1.0"',
                        "api_version = 1",
                        'entrypoint = "worker.py"',
                        'capabilities = ["tool"]',
                        'permissions = ["broker:project_read"]',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (dest / "README.md").write_text(
                "\n".join(
                    [
                        f"# {title}",
                        "",
                        "## Problem",
                        description,
                        "",
                        "## Expected behavior",
                        expected_behavior,
                        "",
                        f"## Scar `{scar_id}`",
                        "Evidence events:",
                        *([f"- `{event_id}`" for event_id in evidence_event_ids] or ["- none"]),
                        "",
                        "## Permissions",
                        "- `broker:project_read` only",
                        "",
                        "## Security notes",
                        "This package is an agent-authored proposal. It is not installed",
                        "and cannot enable itself. A human must `hames plugin install` the",
                        "proposal path, then enable it.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (dest / "worker.py").write_text(_PROPOSAL_WORKER, encoding="utf-8")
            manifest = PluginManifest.model_validate(
                {
                    "id": plugin_id,
                    "name": name,
                    "version": "0.1.0",
                    "api_version": 1,
                    "entrypoint": "worker.py",
                    "capabilities": ["tool"],
                    "permissions": ["broker:project_read"],
                }
            )
            return self.store.record_proposal(
                package_path=dest,
                manifest=manifest,
                session_id=session.id,
                agent_id=session.agent_id,
                scar_id=scar_id,
                proposal_id=proposal_id,
            )

        recorded = await asyncio.to_thread(_write)
        await self._publish_latest(session.id)
        return _proposal_view(recorded)

    async def enable(self, plugin_id: str, *, session: Session | None = None) -> PluginView:
        if not self.config.plugins.enabled:
            raise ValueError("plugins are disabled in configuration")
        selected = session or self.control_session()
        plugin = self.store.get(plugin_id)
        if plugin.enabled and plugin_id in self._handles:
            return self.describe(plugin_id)
        await asyncio.to_thread(
            self.store.set_enabled,
            plugin_id,
            True,
            session_id=selected.id,
            agent_id=selected.agent_id,
        )
        await self._publish_latest(selected.id)
        version = self.store.active_version(plugin_id)
        if version is None:
            raise ValueError("plugin has no installed version")
        try:
            await self._start_worker(version, selected)
        except Exception:
            await asyncio.to_thread(
                self.store.set_enabled,
                plugin_id,
                False,
                session_id=selected.id,
                agent_id=selected.agent_id,
            )
            await self._publish_latest(selected.id)
            raise
        return self.describe(plugin_id)

    async def disable(self, plugin_id: str, *, session: Session | None = None) -> PluginView:
        selected = session or self.control_session()
        if plugin_id in self._handles:
            await self._stop_worker(plugin_id, selected)
        plugin = self.store.get(plugin_id)
        if plugin.enabled:
            await asyncio.to_thread(
                self.store.set_enabled,
                plugin_id,
                False,
                session_id=selected.id,
                agent_id=selected.agent_id,
            )
            await self._publish_latest(selected.id)
        return self.describe(plugin_id)

    async def remove(self, plugin_id: str, *, session: Session | None = None) -> None:
        selected = session or self.control_session()
        if plugin_id in self._handles:
            await self._stop_worker(plugin_id, selected)
        await asyncio.to_thread(
            self.store.remove, plugin_id, session_id=selected.id, agent_id=selected.agent_id
        )
        await self._publish_latest(selected.id)

    async def start_enabled(self) -> None:
        if not self.config.plugins.enabled:
            return
        versions = self.store.enabled_versions()
        if not versions:
            return
        session = self.control_session()
        for version in versions:
            if version.plugin_id in self._handles:
                continue
            try:
                await self._start_worker(version, session)
            except (PluginSandboxError, PluginProtocolError, ValueError, OSError):
                continue

    async def close(self) -> None:
        if not self._handles:
            return
        session: Session | None
        try:
            session = self.control_session()
        except (KeyError, ValueError, OSError):
            session = None
        for plugin_id in list(self._handles):
            try:
                await self._stop_worker(plugin_id, session)
            except Exception:
                handle = self._handles.pop(plugin_id, None)
                if handle is not None:
                    await handle.worker.shutdown()

    async def execute_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        session: Session,
        context: ToolContext,
        allowed_tools: frozenset[str],
        run_id: str | None,
        append: Append,
    ) -> ToolResult:
        plugin_id, tool_name = _split_tool(name)
        handle = self._handles.get(plugin_id)
        if handle is None:
            return ToolResult(status="failed", summary=f"plugin {plugin_id} is not running")
        advertised = {spec.name for spec in handle.tools}
        if tool_name not in advertised:
            return ToolResult(status="failed", summary=f"unknown plugin tool: {name}")
        broker = CapabilityBroker(
            plugin_id=plugin_id,
            permissions=frozenset(handle.version.permissions),
            policy=self.policy,
            session=session,
            context=context,
            allowed_tools=allowed_tools,
            append=append,
            run_id=run_id,
        )
        async with handle.lock:
            handle.broker = broker
            try:
                result = await handle.worker.execute_tool(tool_name, arguments)
            except PluginProtocolError as exc:
                await self._append(
                    session_id=session.id,
                    run_id=run_id,
                    agent_id=session.agent_id,
                    event_type="plugin.worker.failed",
                    payload={
                        "plugin_id": plugin_id,
                        "status": "failed",
                        "message": str(exc),
                    },
                    correlation_id=run_id,
                )
                return ToolResult(status="failed", summary=str(exc))
            finally:
                handle.broker = None
        return ToolResult(
            status="completed",
            summary=result.summary or "plugin completed",
            content=result.content,
            structured_data=result.structured,
        )

    async def collect_context(
        self,
        query: str,
        *,
        session: Session,
        context: ToolContext,
        allowed_tools: frozenset[str],
        run_id: str | None,
        append: Append,
    ) -> list[PluginContextItem]:
        items: list[PluginContextItem] = []
        limit = self.config.plugins.max_output_bytes
        for plugin_id, handle in list(self._handles.items()):
            if not handle.context_sources:
                continue
            broker = CapabilityBroker(
                plugin_id=plugin_id,
                permissions=frozenset(handle.version.permissions),
                policy=self.policy,
                session=session,
                context=context,
                allowed_tools=allowed_tools,
                append=append,
                run_id=run_id,
            )
            async with handle.lock:
                handle.broker = broker
                try:
                    collected = await handle.worker.collect_context(query)
                except PluginProtocolError:
                    continue
                finally:
                    handle.broker = None
            for raw in collected.sources:
                source_id = str(raw.get("id") or plugin_id)
                text = str(raw.get("text") or "")
                encoded = text.encode("utf-8")
                if len(encoded) > limit:
                    text = encoded[:limit].decode("utf-8", errors="ignore")
                items.append(PluginContextItem(plugin_id=plugin_id, source_id=source_id, text=text))
        return items

    async def deliver_event(self, event: Event) -> None:
        if event.type.startswith("plugin."):
            return
        dumped = JSON_OBJECT.validate_python(event.model_dump(mode="json"))
        for handle in list(self._handles.values()):
            if not _event_matches(event.type, handle.event_filters):
                continue
            if handle.lock.locked():
                continue
            async with handle.lock:
                try:
                    await handle.worker.deliver_event(dumped)
                except PluginProtocolError:
                    continue

    async def _start_worker(self, version: PluginVersionRecord, session: Session) -> None:
        allow_unsandboxed = self.config.plugins.allow_unsandboxed_user_plugins
        warning = ""
        if not bwrap_available():
            if not allow_unsandboxed:
                raise PluginSandboxError("plugin isolation is unavailable (bwrap missing)")
            warning = "running unsandboxed because bwrap is missing"
        env_root = self.paths.plugins / "env" / version.plugin_id / version.fingerprint[:12]
        command = worker_command(
            package=Path(version.package_path),
            entrypoint=version.manifest.entrypoint,
            env_root=env_root if env_root.is_dir() else None,
            allow_unsandboxed=allow_unsandboxed,
        )
        cell: list[RunningPlugin] = []

        async def on_broker(method: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
            if not cell:
                raise PermissionError("capability broker is not attached")
            return await cell[0].on_broker(method, arguments)

        worker = await spawn_worker(
            command,
            timeout_seconds=self.config.plugins.worker_timeout_seconds,
            on_broker=on_broker,
        )
        handle = RunningPlugin(version=version, worker=worker, tools=[], warning=warning)
        cell.append(handle)
        try:
            init = await worker.initialize(version.plugin_id, version.version)
            if init.plugin_id != version.plugin_id:
                raise PluginProtocolError(
                    f"worker advertised {init.plugin_id}, expected {version.plugin_id}"
                )
            tools: list[PluginToolSpec] = []
            for spec in init.tools:
                if PLUGIN_TOOL_NAME.fullmatch(spec.name) is None:
                    raise PluginProtocolError(f"invalid plugin tool name: {spec.name}")
                tools.append(spec)
            handle.tools = tools
            handle.context_sources = list(init.context_sources)
            handle.event_filters = list(init.event_filters)
        except Exception as exc:
            await worker.shutdown()
            await self._append(
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="plugin.worker.failed",
                payload={
                    "plugin_id": version.plugin_id,
                    "status": "failed",
                    "message": str(exc),
                },
                correlation_id=version.plugin_id,
            )
            raise
        self._handles[version.plugin_id] = handle
        await self._append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="plugin.worker.started",
            payload={"plugin_id": version.plugin_id, "status": "started"},
            correlation_id=version.plugin_id,
        )
        await self._append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="plugin.capability.registered",
            payload={
                "plugin_id": version.plugin_id,
                "version_id": version.id,
                "version": version.version,
                "fingerprint": version.fingerprint,
                "permissions": list(version.permissions),
                "enabled": True,
            },
            correlation_id=version.plugin_id,
        )

    async def _stop_worker(self, plugin_id: str, session: Session | None) -> None:
        handle = self._handles.pop(plugin_id, None)
        if handle is None:
            return
        await handle.worker.shutdown()
        if session is None:
            return
        await self._append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="plugin.worker.stopped",
            payload={"plugin_id": plugin_id, "status": "stopped"},
            correlation_id=plugin_id,
        )

    async def _append(self, **kwargs: Any) -> Event:
        event = await asyncio.to_thread(self.ledger.append, **kwargs)
        await self.events.publish(
            event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
        )
        return event

    async def _publish_latest(self, session_id: str) -> None:
        events = await asyncio.to_thread(self.ledger.list_events, session_id)
        if not events:
            return
        latest = events[-1]
        await self.events.publish(
            session_id, {"durable": True, "event": latest.model_dump(mode="json")}
        )


def _proposal_view(proposal: PluginProposalRecord) -> PluginProposalView:
    return PluginProposalView(
        id=proposal.id,
        plugin_id=proposal.plugin_id or "",
        scar_id=proposal.scar_id or "",
        status=proposal.status,
        package_path=proposal.package_path,
        permissions=list(proposal.manifest.permissions),
        created_at=proposal.created_at,
    )


def _inspect_view(inspected: InspectedPlugin) -> PluginInspectView:
    return PluginInspectView(
        id=inspected.manifest.id,
        name=inspected.manifest.name,
        version=inspected.manifest.version,
        fingerprint=inspected.fingerprint,
        permissions=list(inspected.manifest.permissions),
        capabilities=list(inspected.manifest.capabilities),
        entrypoint=inspected.manifest.entrypoint,
        files=list(inspected.files),
    )


def _split_tool(name: str) -> tuple[str, str]:
    if name.count(".") != 1:
        raise ValueError(f"invalid plugin tool id: {name}")
    plugin_id, tool_name = name.split(".")
    return plugin_id, tool_name


def _event_matches(event_type: str, filters: list[str]) -> bool:
    return "*" in filters or event_type in filters
