from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers.fake import FakeProvider
from hames.web_ui import WebUi


@pytest.mark.asyncio
async def test_gateway_owns_authenticated_web_shell(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state_paths = HamesPaths.resolve(root=home)
    state = GatewayState.create(state_paths, providers={"fake": FakeProvider([])})
    assets = tmp_path / "web-dist"
    (assets / "assets").mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html><title>Hames Web</title>", encoding="utf-8")
    (assets / "assets" / "app-abc123.js").write_text("export {};", encoding="utf-8")
    state.web = WebUi(state.config.gateway, asset_root=assets)
    app = create_app(state)
    headers = {"Authorization": f"Bearer {state.token}"}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:7411",
            follow_redirects=False,
        ) as client:
            shell = await client.get("/chat")
            assert shell.status_code == 200
            assert shell.headers["cache-control"] == "no-store"
            assert "frame-ancestors 'none'" in shell.headers["content-security-policy"]
            assert "img-src 'self' data: blob:" in shell.headers["content-security-policy"]

            asset = await client.get("/assets/app-abc123.js")
            assert asset.status_code == 200
            assert "immutable" in asset.headers["cache-control"]

            launch = await client.post(
                "/v1/web/launch",
                headers=headers,
                json={"working_directory": str(tmp_path)},
            )
            assert launch.status_code == 200
            launch_url = str(launch.json()["url"])
            launch_path = urlsplit(launch_url).path

            rejected = await client.get(launch_path.replace(launch_path.rsplit("/", 1)[1], "bad"))
            assert rejected.status_code == 401

            exchanged = await client.get(launch_path)
            assert exchanged.status_code == 303
            assert exchanged.headers["location"] == "/chat"
            cookie = exchanged.headers["set-cookie"]
            assert "HttpOnly" in cookie
            assert "SameSite=strict" in cookie

            reused = await client.get(launch_path)
            assert reused.status_code == 401

            missing_workspace = await client.post(
                "/v1/web/launch",
                headers=headers,
                json={"working_directory": str(tmp_path / "missing")},
            )
            assert missing_workspace.status_code == 422
            assert missing_workspace.json()["error"]["code"] == "invalid_working_directory"

            bootstrap = await client.get("/_hames/v1/bootstrap")
            assert bootstrap.status_code == 200
            bootstrap_body = bootstrap.json()
            assert bootstrap_body["working_directory"] == str(tmp_path)
            assert bootstrap_body["gateway_protocol_version"] > 0
            csrf = str(bootstrap_body["csrf_token"])

            browser_api = await client.get("/v1/providers")
            assert browser_api.status_code == 200

            missing_csrf = await client.post(
                "/v1/sessions",
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            assert missing_csrf.status_code == 403

            created = await client.post(
                "/v1/sessions",
                headers={"Origin": state.web.origin, "X-Hames-CSRF": csrf},
                json={
                    "working_directory": str(tmp_path),
                    "provider": "fake",
                    "model": "fixture",
                },
            )
            assert created.status_code == 201
            assert state.workspaces.list() == []
            visible_sessions = await client.get(
                "/v1/sessions",
                params={"registered_workspaces_only": True},
            )
            assert visible_sessions.status_code == 200
            assert visible_sessions.json() == []

            foreign_host = await client.get(
                "/_hames/v1/bootstrap",
                headers={"Host": "foreign.invalid"},
            )
            assert foreign_host.status_code == 421
    finally:
        await state.runs.close()
        assert state_paths.database.exists()
