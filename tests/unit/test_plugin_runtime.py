from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from hames.blobs import BlobStore
from hames.broker import EventBroker
from hames.config import HamesConfig, PluginsConfig, ToolsConfig
from hames.ledger import Event, Ledger
from hames.paths import HamesPaths
from hames.plugin_runtime import PluginManager
from hames.policy import PolicyGate
from hames.tools import ToolContext

WORKER = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "loopback_worker.py"


def _package(root: Path) -> Path:
    package = root / "project-stats"
    package.mkdir()
    (package / "plugin.toml").write_text(
        "\n".join(
            [
                'id = "project-stats"',
                'name = "Project Stats"',
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
    shutil.copy(WORKER, package / "worker.py")
    return package


def _manager(hames_paths: HamesPaths) -> PluginManager:
    hames_paths.ensure_foundation()
    ledger = Ledger.open(hames_paths.database)
    return PluginManager(
        paths=hames_paths,
        ledger=ledger,
        config=HamesConfig(plugins=PluginsConfig(allow_unsandboxed_user_plugins=True)),
        events=EventBroker(),
        policy=PolicyGate(hames_paths.root),
    )


def _context(tmp_path: Path) -> ToolContext:
    project = tmp_path / "project"
    project.mkdir()
    (project / "readme.md").write_text("hello\n", encoding="utf-8")
    return ToolContext(
        project_root=project,
        scratch_root=tmp_path / "scratch",
        blobs=BlobStore(tmp_path / "blobs"),
        config=ToolsConfig(),
    )


@pytest.mark.asyncio
async def test_install_stays_disabled_until_enable(hames_paths: HamesPaths, tmp_path: Path) -> None:
    manager = _manager(hames_paths)
    try:
        installed = await manager.install(_package(tmp_path))
        assert installed.enabled is False
        assert installed.running is False
        assert manager.names() == set()
        listed = manager.list_plugins()
        assert [item.id for item in listed] == ["project-stats"]
        enabled = await manager.enable("project-stats")
        assert enabled.enabled is True
        assert enabled.running is True
        assert enabled.tools == ["project-stats.summary"]
        assert manager.names() == {"project-stats.summary"}
        definitions = manager.definitions(frozenset({"project-stats.summary"}))
        assert definitions[0].name == "project-stats.summary"
        disabled = await manager.disable("project-stats")
        assert disabled.enabled is False
        assert disabled.running is False
        assert manager.names() == set()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_enabled_tool_executes_through_the_worker(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    manager = _manager(hames_paths)
    context = _context(tmp_path)
    try:
        await manager.install(_package(tmp_path))
        await manager.enable("project-stats")
        session = manager.control_session()

        async def append(**kwargs: Any) -> Event:
            return manager.ledger.append(**kwargs)

        result = await manager.execute_tool(
            "project-stats.summary",
            {},
            session=session,
            context=context,
            allowed_tools=frozenset({"read_file", "list_dir", "project-stats.summary"}),
            run_id="run-1",
            append=append,
        )
        assert result.status == "completed"
        assert result.summary == "ok"
        brokered = await manager.execute_tool(
            "project-stats.summary",
            {"use_broker": True},
            session=session,
            context=context,
            allowed_tools=frozenset({"read_file", "list_dir", "project-stats.summary"}),
            run_id="run-1",
            append=append,
        )
        assert brokered.status == "completed"
        assert brokered.summary == "listed"
        types = [event.type for event in manager.ledger.list_events(session.id)]
        assert "plugin.broker.requested" in types
        assert "plugin.worker.started" in types
        assert "plugin.capability.registered" in types
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_remove_stops_the_worker(hames_paths: HamesPaths, tmp_path: Path) -> None:
    manager = _manager(hames_paths)
    try:
        await manager.install(_package(tmp_path))
        await manager.enable("project-stats")
        await manager.remove("project-stats")
        assert manager.names() == set()
        removed = manager.describe("project-stats")
        assert removed.enabled is False
        assert removed.running is False
        assert removed.version == ""
        assert removed.tools == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_collect_context_and_filtered_event_deliver(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    manager = _manager(hames_paths)
    context = _context(tmp_path)
    try:
        await manager.install(_package(tmp_path))
        await manager.enable("project-stats")
        session = manager.control_session()

        async def append(**kwargs: Any) -> Event:
            return manager.ledger.append(**kwargs)

        sources = await manager.collect_context(
            "files",
            session=session,
            context=context,
            allowed_tools=frozenset({"read_file", "list_dir"}),
            run_id="run-1",
            append=append,
        )
        assert sources[0].plugin_id == "project-stats"
        assert sources[0].source_id == "project-stats"
        assert "file count" in sources[0].text
        before = len(manager.ledger.list_events(session.id))
        matching = manager.ledger.append(
            session_id=session.id,
            event_type="tool.completed",
            payload={
                "tool_call_id": "t1",
                "name": "list_dir",
                "status": "completed",
                "summary": "listed",
                "content": "",
            },
        )
        await manager.deliver_event(matching)
        skipped = manager.ledger.append(
            session_id=session.id,
            event_type="user.message",
            payload={"content": "hi"},
        )
        await manager.deliver_event(skipped)
        after = manager.ledger.list_events(session.id)
        assert len(after) == before + 2
        assert [event.type for event in after[-2:]] == ["tool.completed", "user.message"]
    finally:
        await manager.close()
