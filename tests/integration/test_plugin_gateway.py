from __future__ import annotations

import shutil
from pathlib import Path
from typing import cast

import httpx
import pytest

from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers.base import JSON_OBJECT, JsonValue
from hames.providers.fake import FakeProvider

WORKER = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "loopback_worker.py"


def response_object(response: httpx.Response) -> dict[str, JsonValue]:
    return JSON_OBJECT.validate_python(cast(object, response.json()))


def _package(root: Path) -> Path:
    package = root / "project-stats"
    package.mkdir()
    (package / "plugin.toml").write_text(
        'id = "project-stats"\nname = "Project Stats"\nversion = "0.1.0"\n'
        'api_version = 1\nentrypoint = "worker.py"\ncapabilities = ["tool"]\n'
        'permissions = ["broker:project_read"]\n',
        encoding="utf-8",
    )
    shutil.copy(WORKER, package / "worker.py")
    return package


@pytest.mark.asyncio
async def test_gateway_install_enable_and_disable(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[plugins]\nallow_unsandboxed_user_plugins = true\n",
        encoding="utf-8",
    )
    package = _package(tmp_path)
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    headers = {"Authorization": f"Bearer {state.token}"}
    transport = httpx.ASGITransport(app=create_app(state))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inspected = await client.post(
                "/v1/plugins/inspect",
                headers=headers,
                json={"path": str(package)},
            )
            assert inspected.status_code == 200
            body = response_object(inspected)
            assert body["id"] == "project-stats"
            assert body["permissions"] == ["broker:project_read"]

            installed = await client.post(
                "/v1/plugins/install",
                headers=headers,
                json={"path": str(package)},
            )
            assert installed.status_code == 201
            installed_body = response_object(installed)
            assert installed_body["enabled"] is False
            assert installed_body["running"] is False

            listed = await client.get("/v1/plugins", headers=headers)
            assert [item["id"] for item in listed.json()] == ["project-stats"]

            enabled = await client.post("/v1/plugins/project-stats/enable", headers=headers)
            assert enabled.status_code == 200
            enabled_body = response_object(enabled)
            assert enabled_body["enabled"] is True
            assert enabled_body["running"] is True
            assert enabled_body["tools"] == ["project-stats.summary"]

            disabled = await client.post("/v1/plugins/project-stats/disable", headers=headers)
            assert response_object(disabled)["running"] is False
    finally:
        await state.plugins.close()
