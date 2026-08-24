from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hames.blobs import BlobStore
from hames.config import ToolsConfig
from hames.ledger import Event, Ledger, Session
from hames.paths import HamesPaths
from hames.plugin_broker import CapabilityBroker
from hames.policy import PolicyGate
from hames.tools import ToolContext


def _context(tmp_path: Path) -> ToolContext:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "readme.md").write_text("hello\n", encoding="utf-8")
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    return ToolContext(
        project_root=project,
        scratch_root=tmp_path / "scratch",
        blobs=BlobStore(tmp_path / "blobs"),
        config=ToolsConfig(),
    )


def _broker(
    tmp_path: Path,
    hames_paths: HamesPaths,
    *,
    permissions: frozenset[str],
    allowed_tools: frozenset[str] | None = None,
) -> tuple[CapabilityBroker, Ledger, Session]:
    hames_paths.ensure_foundation()
    context = _context(tmp_path)
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=context.project_root,
        agent_id="default",
        provider="fake",
        model="fixture",
    )

    async def append(**kwargs: Any) -> Event:
        return ledger.append(**kwargs)

    broker = CapabilityBroker(
        plugin_id="project-stats",
        permissions=permissions,
        policy=PolicyGate(hames_paths.root),
        session=session,
        context=context,
        allowed_tools=allowed_tools or frozenset({"read_file", "list_dir", "write_file", "shell"}),
        append=append,
        run_id="run-1",
    )
    return broker, ledger, session


def _types(ledger: Ledger, session: Session) -> list[str]:
    return [event.type for event in ledger.list_events(session.id)]


@pytest.mark.asyncio
async def test_missing_permission_is_denied_before_tools(
    tmp_path: Path, hames_paths: HamesPaths
) -> None:
    broker, ledger, session = _broker(tmp_path, hames_paths, permissions=frozenset())
    with pytest.raises(PermissionError, match="lacks broker:project_read"):
        await broker.call("project.list", {"path": "."})
    types = _types(ledger, session)
    assert types.count("plugin.broker.requested") == 1
    assert types.count("plugin.broker.completed") == 1
    assert "policy.requested" not in types
    completed = [
        event for event in ledger.list_events(session.id) if event.type.endswith("completed")
    ]
    assert completed[-1].payload["status"] == "denied"


@pytest.mark.asyncio
async def test_unknown_method_is_rejected(tmp_path: Path, hames_paths: HamesPaths) -> None:
    broker, ledger, session = _broker(
        tmp_path, hames_paths, permissions=frozenset({"broker:project_read"})
    )
    with pytest.raises(ValueError, match="unknown broker method"):
        await broker.call("kernel.eval", {})
    assert "plugin.broker.requested" not in _types(ledger, session)


@pytest.mark.asyncio
async def test_network_request_is_denied_by_default(
    tmp_path: Path, hames_paths: HamesPaths
) -> None:
    broker, ledger, session = _broker(
        tmp_path, hames_paths, permissions=frozenset({"broker:network_request"})
    )
    with pytest.raises(PermissionError, match="denied by default"):
        await broker.call("network.request", {"url": "https://example.com"})
    types = _types(ledger, session)
    assert types.count("plugin.broker.requested") == 1
    assert types.count("plugin.broker.completed") == 1
    assert "policy.requested" not in types


@pytest.mark.asyncio
async def test_project_list_and_read_go_through_policy(
    tmp_path: Path, hames_paths: HamesPaths
) -> None:
    broker, ledger, session = _broker(
        tmp_path, hames_paths, permissions=frozenset({"broker:project_read"})
    )
    listed = await broker.call("project.list", {"path": "."})
    assert listed["status"] == "completed"
    assert "readme.md" in str(listed["content"])
    read = await broker.call("project.read", {"path": "readme.md"})
    assert read["content"] == "hello\n"
    types = _types(ledger, session)
    assert types.count("plugin.broker.requested") == 2
    assert types.count("plugin.broker.completed") == 2
    assert types.count("policy.requested") == 2
    assert types.count("policy.decided") == 2
    decided = [event for event in ledger.list_events(session.id) if event.type == "policy.decided"]
    assert {event.payload["decision"] for event in decided} == {"allow"}
    completed = [
        event for event in ledger.list_events(session.id) if event.type == "plugin.broker.completed"
    ]
    assert {event.payload["status"] for event in completed} == {"completed"}


@pytest.mark.asyncio
async def test_policy_denies_secret_files(tmp_path: Path, hames_paths: HamesPaths) -> None:
    broker, ledger, session = _broker(
        tmp_path, hames_paths, permissions=frozenset({"broker:project_read"})
    )
    (broker.context.project_root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="secret"):
        await broker.call("project.read", {"path": ".env"})
    types = _types(ledger, session)
    assert "policy.decided" in types
    decided = [event for event in ledger.list_events(session.id) if event.type == "policy.decided"]
    assert decided[-1].payload["decision"] == "deny"


@pytest.mark.asyncio
async def test_write_requires_write_permission(tmp_path: Path, hames_paths: HamesPaths) -> None:
    broker, _, _ = _broker(tmp_path, hames_paths, permissions=frozenset({"broker:project_read"}))
    with pytest.raises(PermissionError, match="lacks broker:project_write"):
        await broker.call("project.write", {"path": "out.txt", "content": "x\n"})


@pytest.mark.asyncio
async def test_allowed_tools_can_block_mapped_tools(
    tmp_path: Path, hames_paths: HamesPaths
) -> None:
    broker, _, _ = _broker(
        tmp_path,
        hames_paths,
        permissions=frozenset({"broker:project_read"}),
        allowed_tools=frozenset({"write_file"}),
    )
    with pytest.raises(PermissionError, match="not allowed to use this tool"):
        await broker.call("project.list", {"path": "."})


@pytest.mark.asyncio
async def test_high_risk_shell_is_denied_not_confirmed(
    tmp_path: Path, hames_paths: HamesPaths
) -> None:
    broker, ledger, session = _broker(
        tmp_path, hames_paths, permissions=frozenset({"broker:process_run_scoped"})
    )
    with pytest.raises(PermissionError, match="recursive deletion"):
        await broker.call("process.run_scoped", {"command": "rm -rf target"})
    decided = [event for event in ledger.list_events(session.id) if event.type == "policy.decided"]
    assert decided[-1].payload["decision"] == "require_confirmation"
