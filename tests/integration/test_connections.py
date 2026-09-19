from pathlib import Path

import httpx
import pytest

from hames.config import load_config
from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers import ProviderError, ProviderModel
from hames.providers.deepseek import DeepSeekProvider
from hames.providers.fake import FakeProvider
from hames.providers.openai import OpenAIProvider
from hames.providers.registry import configured_providers
from hames.providers.xai import XaiProvider


@pytest.mark.parametrize(
    ("profile_id", "provider_class", "env_name", "model_id"),
    [
        ("deepseek", DeepSeekProvider, "DEEPSEEK_API_KEY", "deepseek-flash"),
        ("openai", OpenAIProvider, "OPENAI_API_KEY", "gpt-4.1"),
        ("xai", XaiProvider, "XAI_API_KEY", "grok-4"),
    ],
)
@pytest.mark.asyncio
async def test_connections_verify_save_reload_disconnect_and_keep_secrets_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile_id: str,
    provider_class: type[OpenAIProvider],
    env_name: str,
    model_id: str,
) -> None:
    monkeypatch.delenv(env_name, raising=False)
    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path), providers={"fake": FakeProvider([])}
    )

    async def models(self: OpenAIProvider) -> list[ProviderModel]:
        assert self._headers()["Authorization"] == "Bearer test-private-key"  # pyright: ignore[reportPrivateUsage]
        return [ProviderModel(id=model_id, provider=self.profile_id, status="available")]

    monkeypatch.setattr(provider_class, "list_models", models)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {state.token}"},
        ) as client:
            initial = await client.get("/v1/connections")
            assert initial.status_code == 200
            assert any(
                row["id"] == "zai_coding" and row["status"] == "not_connected"
                for row in initial.json()
            )
            connected = await client.post(
                f"/v1/connections/{profile_id}", json={"key": "test-private-key"}
            )
            assert connected.status_code == 200, connected.text
            assert "test-private-key" not in connected.text
            row = next(row for row in connected.json() if row["id"] == profile_id)
            assert row["status"] == "connected"
            assert row["models"] == [model_id]
            assert row["can_disconnect"]
            key = tmp_path / f"credentials/{profile_id}.key"
            assert key.stat().st_mode & 0o777 == 0o600
            assert key.parent.stat().st_mode & 0o777 == 0o700
            assert "test-private-key" not in (tmp_path / "connections.json").read_text()
            config = load_config(state.paths, environ={})
            assert config.providers[profile_id].api_key_file == str(key)
            restored = configured_providers(config)
            try:
                assert (await restored[profile_id].list_models())[0].id == model_id
            finally:
                for provider in restored.values():
                    await provider.aclose()
            assert profile_id in state.providers
            assert (await client.post(f"/v1/connections/{profile_id}/test")).status_code == 200
            disconnected = await client.delete(f"/v1/connections/{profile_id}")
            assert disconnected.status_code == 200
            assert not key.exists()
            row = next(row for row in disconnected.json() if row["id"] == profile_id)
            assert row["status"] == "not_connected"
            assert row["can_connect"]
            assert not row["can_disconnect"]
    finally:
        await state.runs.close()
        for provider in state.providers.values():
            await provider.aclose()


@pytest.mark.asyncio
async def test_connections_reject_bad_keys_without_echoing_or_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path), providers={"fake": FakeProvider([])}
    )

    async def denied(self: DeepSeekProvider) -> list[ProviderModel]:
        raise ProviderError("auth", "bad secret-value-key")

    monkeypatch.setattr(DeepSeekProvider, "list_models", denied)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {state.token}"},
        ) as client:
            for value in [
                "secret-value-key",
                "secret-value-key\nheader",
                12,
                {"secret-value-key": 1},
            ]:
                response = await client.post("/v1/connections/deepseek", json={"key": value})
                assert response.status_code == 400
                assert "secret-value-key" not in response.text
            assert not (tmp_path / "connections.json").exists()
            assert not (tmp_path / "credentials/deepseek.key").exists()
            response = await client.get(
                "/v1/connections", headers={"Authorization": "Bearer wrong"}
            )
            assert response.status_code == 401
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_connections_respect_environment_and_active_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path), providers={"fake": FakeProvider([])}
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {state.token}"},
        ) as client:
            monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")
            response = await client.post("/v1/connections/deepseek", json={"key": "replacement"})
            assert response.status_code == 409
            assert not (tmp_path / "credentials/deepseek.key").exists()
            monkeypatch.delenv("DEEPSEEK_API_KEY")

            async def models(self: DeepSeekProvider) -> list[ProviderModel]:
                return [
                    ProviderModel(id="deepseek-flash", provider=self.profile_id, status="available")
                ]

            monkeypatch.setattr(DeepSeekProvider, "list_models", models)
            assert (
                await client.post("/v1/connections/deepseek", json={"key": "first-key"})
            ).status_code == 200
            monkeypatch.setattr(type(state.runs), "active_run_count", property(lambda _: 1))
            assert (await client.delete("/v1/connections/deepseek")).status_code == 409
            assert (
                await client.post("/v1/connections/deepseek", json={"key": "new-key"})
            ).status_code == 409
            assert (tmp_path / "credentials/deepseek.key").read_text().strip() == "first-key"
    finally:
        await state.runs.close()
        for provider in state.providers.values():
            await provider.aclose()


@pytest.mark.asyncio
async def test_connection_names_distinguish_api_and_subscription(tmp_path: Path) -> None:
    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path),
        providers={"codex": FakeProvider([]), "grok": FakeProvider([])},
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {state.token}"},
        ) as client:
            response = await client.get("/v1/connections")
            rows = {row["id"]: row for row in response.json()}
            assert rows["openai"]["name"] == "OpenAI (API)"
            assert rows["xai"]["name"] == "Grok (API)"
            assert rows["codex"]["name"] == "Codex"
            assert rows["grok"]["name"] == "Grok Build"
            assert rows["openai"]["can_connect"]
            assert rows["xai"]["can_connect"]
            assert not rows["codex"]["can_connect"]
            assert not rows["grok"]["can_connect"]
    finally:
        await state.runs.close()
