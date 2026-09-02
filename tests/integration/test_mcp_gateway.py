from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import httpx
import pytest

from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers.fake import FakeProvider

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_server.py"


@pytest.mark.asyncio
async def test_gateway_manages_stdio_mcp_server_and_broadcasts_notices(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with (
            state.broker.subscribe("observing-session") as notices,
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            added = await client.post(
                "/v1/mcp/servers",
                headers=headers,
                json={
                    "id": "fixture",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(FIXTURE)],
                },
            )
            assert added.status_code == 201
            assert added.json()["status"] == "disabled"
            notice = await notices.get()
            assert notice["event_type"] == "runtime.notice"
            payload = cast(dict[str, object], notice["payload"])
            assert payload["code"] == "mcp.server.added"

            enabled = await client.post("/v1/mcp/servers/fixture/enable", headers=headers)
            assert enabled.status_code == 200
            enabled_body = enabled.json()
            assert enabled_body["status"] == "ready"
            assert {tool["exposed_name"] for tool in enabled_body["tools"]} == {
                "mcp__fixture__change",
                "mcp__fixture__echo",
            }

            inspected = await client.post("/v1/mcp/servers/fixture/inspect", headers=headers)
            assert inspected.status_code == 200
            assert inspected.json()["server_name"] == "fixture-mcp"

            listed = await client.get("/v1/mcp/servers", headers=headers)
            assert listed.status_code == 200
            assert [server["id"] for server in listed.json()] == ["fixture"]

            disabled = await client.post("/v1/mcp/servers/fixture/disable", headers=headers)
            assert disabled.status_code == 200
            assert disabled.json()["status"] == "disabled"

            removed = await client.delete("/v1/mcp/servers/fixture", headers=headers)
            assert removed.status_code == 200
            assert removed.json() == {"removed": True}
    finally:
        await state.runs.close()
