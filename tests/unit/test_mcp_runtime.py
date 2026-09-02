from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hames.blobs import BlobStore
from hames.config import ToolsConfig
from hames.database import MIGRATIONS, Database
from hames.mcp_runtime import McpManager, McpServerSpec, McpStore
from hames.policy import PolicyDecisionKind, PolicyGate
from hames.tools import McpToolArguments, ToolContext

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_server.py"


def test_mcp_registry_is_migration_eighteen(tmp_path: Path) -> None:
    path = tmp_path / "hames.db"
    Database(path, migrations=MIGRATIONS[:17]).migrate()
    Database(path).migrate()
    store = McpStore(Database(path))
    spec = McpServerSpec(id="fixture", transport="stdio", command=sys.executable)
    store.add(spec)
    assert store.get("fixture") == (spec, False)
    store.set_enabled("fixture", True)
    assert store.get("fixture")[1] is True
    store.remove("fixture")
    assert store.list() == []


def test_mcp_specs_store_only_environment_references() -> None:
    spec = McpServerSpec(
        id="remote",
        transport="http",
        url="https://example.com/mcp",
        headers={"Authorization": "MCP_AUTH_HEADER"},
    )
    assert spec.headers == {"Authorization": "MCP_AUTH_HEADER"}
    with pytest.raises(ValueError, match="credentials"):
        McpServerSpec(
            id="remote",
            transport="http",
            url="https://secret@example.com/mcp",
        )


@pytest.mark.asyncio
async def test_stdio_server_exposes_tools_and_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "hames.db")
    database.migrate()
    notices: list[tuple[str, str, str, bool, str | None]] = []

    async def notice(
        server_id: str, code: str, message: str, error: bool, session_id: str | None
    ) -> None:
        notices.append((server_id, code, message, error, session_id))

    monkeypatch.setenv("SOURCE_FIXTURE_VALUE", "forwarded-value")
    manager = McpManager(database, notice)
    context = ToolContext(
        project_root=tmp_path,
        scratch_root=tmp_path / "scratch",
        blobs=BlobStore(tmp_path / "blobs"),
        config=ToolsConfig(),
    )
    try:
        added = await manager.add(
            McpServerSpec(
                id="fixture",
                transport="stdio",
                command=sys.executable,
                args=[str(FIXTURE)],
                env={"FIXTURE_VALUE": "SOURCE_FIXTURE_VALUE"},
            )
        )
        assert added.status == "disabled"
        enabled = await manager.enable("fixture")
        assert enabled.status == "ready"
        assert {tool.name for tool in enabled.tools} == {"echo", "change"}
        echo = next(tool for tool in enabled.tools if tool.name == "echo")
        change = next(tool for tool in enabled.tools if tool.name == "change")
        assert echo.read_only is True
        assert change.destructive is True
        outcome = await manager.call_tool(
            echo.exposed_name, {"text": "hi"}, context, session_id="session-1"
        )
        assert outcome.failed is False
        assert outcome.structured_data == {"forwarded": "forwarded-value", "text": "hi"}
        listed = await manager.list_resources("fixture")
        assert listed["servers"][0]["resources"][0]["uri"] == "fixture://hello"  # type: ignore[index]
        resource = await manager.read_resource("fixture", "fixture://hello", context)
        assert resource.content == "hello from an MCP resource"
        assert any(code == "mcp.connection.ready" for _, code, *_ in notices)
    finally:
        await manager.close()


def test_mcp_annotation_policy_matrix(tmp_path: Path) -> None:
    context = ToolContext(
        project_root=tmp_path,
        scratch_root=tmp_path / "scratch",
        blobs=BlobStore(tmp_path / "blobs"),
        config=ToolsConfig(),
    )
    gate = PolicyGate(tmp_path / ".hames")
    arguments = McpToolArguments.model_validate({"value": "x"})
    assert (
        gate.decide(
            "mcp__fixture__echo",
            arguments,
            context,
            interaction_mode="plan",
            mcp_read_only=True,
        ).decision
        is PolicyDecisionKind.ALLOW
    )
    assert (
        gate.decide(
            "mcp__fixture__change",
            arguments,
            context,
            interaction_mode="auto",
            mcp_read_only=False,
        ).decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )
    assert (
        gate.decide(
            "mcp__fixture__change",
            arguments,
            context,
            interaction_mode="plan",
            mcp_read_only=False,
        ).decision
        is PolicyDecisionKind.DENY
    )
