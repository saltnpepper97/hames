"""Local provider connections. Secrets never enter config, events, or responses."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Request

from hames.config import ProviderProfileConfig
from hames.providers import ProviderError, ProviderModel
from hames.providers.deepseek import DeepSeekProvider
from hames.providers.openai import OpenAIProvider
from hames.providers.scheduled import ScheduledProvider
from hames.providers.xai import XaiProvider
from hames.providers.zai import ZaiCodingProvider, ZaiProvider

if TYPE_CHECKING:
    from hames.gateway import GatewayState

PRESETS = {
    "openai": (
        "OpenAI (API)",
        OpenAIProvider,
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        "",
        "https://platform.openai.com/api-keys",
    ),
    "xai": (
        "Grok (API)",
        XaiProvider,
        "https://api.x.ai/v1",
        "XAI_API_KEY",
        "",
        "https://console.x.ai",
    ),
    "deepseek": (
        "DeepSeek (API)",
        DeepSeekProvider,
        "https://api.deepseek.com",
        "DEEPSEEK_API_KEY",
        "deepseek-flash",
        "https://platform.deepseek.com/api_keys",
    ),
    "zai": (
        "Z.ai (API)",
        ZaiProvider,
        "https://api.z.ai/api/paas/v4",
        "ZAI_API_KEY",
        "glm-5.3",
        "https://z.ai/manage-apikey/apikey-list",
    ),
    "zai_coding": (
        "Z.ai Coding Plan",
        ZaiCodingProvider,
        "https://api.z.ai/api/coding/paas/v4",
        "ZAI_API_KEY",
        "glm-5.3",
        "https://z.ai/manage-apikey/apikey-list",
    ),
}


def atomic_private_write(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".connection-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def model_result(models: list[ProviderModel]) -> dict[str, object]:
    documented = any(model.status == "configured" for model in models)
    return {
        "status": "not_checked" if documented else "connected" if models else "unavailable",
        "model_source": "documented" if documented else "discovered",
        "models": [model.id for model in models],
    }


def install_connections(app: FastAPI, state: GatewayState, auth: list[Any]) -> None:
    from hames.gateway import ApiError

    lock = asyncio.Lock()
    results: dict[str, dict[str, object]] = {}
    metadata_path = state.paths.root / "connections.json"

    def busy() -> bool:
        return bool(
            state.runs.active_run_count
            or any(
                isinstance(provider, ScheduledProvider) and provider.busy
                for provider in state.providers.values()
            )
        )

    def key_path(profile_id: str) -> Path:
        return state.paths.root / "credentials" / f"{profile_id}.key"

    def rows() -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for profile_id in sorted(set(state.providers) | set(PRESETS)):
            preset = PRESETS.get(profile_id)
            profile = state.config.providers.get(profile_id)
            env_name = profile.api_key_env if profile else preset[3] if preset else ""
            external = bool(env_name and os.environ.get(env_name))
            managed = bool(
                preset
                and (
                    not profile
                    or (
                        profile.adapter == profile_id
                        and profile.base_url.rstrip("/") == preset[2]
                        and profile.api_key_file == str(key_path(profile_id))
                        and profile.api_key_env == preset[3]
                    )
                )
            )
            saved = bool(
                managed and key_path(profile_id).is_file() and key_path(profile_id).stat().st_size
            )
            connected = results.get(profile_id, {})
            entries.append(
                {
                    "id": profile_id,
                    "name": preset[0]
                    if preset
                    else {
                        "codex": "Codex",
                        "grok": "Grok Build",
                    }.get(profile_id, profile_id.replace("_", " ")),
                    "status": connected.get(
                        "status",
                        "not_checked"
                        if profile and (not managed or saved or external)
                        else "not_connected",
                    ),
                    "models": connected.get("models", []),
                    "model_source": connected.get("model_source", ""),
                    "can_connect": managed and not external,
                    "can_disconnect": managed and saved and not external,
                    "configured": profile_id in state.providers,
                    "source": "environment"
                    if external
                    else "saved"
                    if saved
                    else "configuration"
                    if profile
                    else "",
                    "key_url": preset[5] if preset else "",
                }
            )
        return entries

    @app.get("/v1/connections", dependencies=auth)
    async def list_connections() -> list[dict[str, object]]:
        return rows()

    @app.post("/v1/connections/{profile_id}/test", dependencies=auth)
    async def test_connection(profile_id: str) -> list[dict[str, object]]:
        async with lock:
            provider = state.providers.get(profile_id)
            if provider is None:
                raise ApiError(404, "unknown_provider", "Connect this provider first.")
            try:
                models = await asyncio.wait_for(provider.list_models(), timeout=30)
                results[profile_id] = model_result(models)
            except (ProviderError, TimeoutError):
                results[profile_id] = {"status": "unavailable", "models": []}
            return rows()

    @app.post("/v1/connections/{profile_id}", dependencies=auth)
    async def connect(profile_id: str, request: Request) -> list[dict[str, object]]:
        async with lock:
            row = next((row for row in rows() if row["id"] == profile_id), None)
            if not row or not row["can_connect"]:
                raise ApiError(
                    409,
                    "connection_managed_externally",
                    "This connection is managed outside Settings.",
                )
            try:
                body = await request.json()
            except ValueError:
                raise ApiError(400, "invalid_key", "Enter an API key.") from None
            key = cast(dict[str, object], body).get("key") if isinstance(body, dict) else None
            if (
                not isinstance(key, str)
                or not 1 <= len(key.strip()) <= 4096
                or not key.strip().isascii()
                or any(ord(c) < 33 or ord(c) > 126 for c in key.strip())
            ):
                raise ApiError(400, "invalid_key", "Enter a valid API key.")
            key = key.strip()
            _, provider_class, base_url, env_name, default_model, _ = PRESETS[profile_id]
            candidate = provider_class(
                base_url,
                profile_id=profile_id,
                api_key_env=env_name,
                environ={env_name: key},
                timeout_seconds=30,
            )
            try:
                models = await asyncio.wait_for(candidate.list_models(), timeout=30)
            except (ProviderError, TimeoutError):
                raise ApiError(
                    400,
                    "connection_failed",
                    "Could not verify this key. Check the key, account access, and try again.",
                ) from None
            finally:
                await candidate.aclose()
            if not models:
                raise ApiError(400, "no_models", "This account returned no supported models.")
            # Re-check after the network await: an existing run may have started.
            if busy() and profile_id in state.providers:
                raise ApiError(
                    409,
                    "provider_busy",
                    "Wait for active work to finish before replacing this connection.",
                )
            profile = ProviderProfileConfig(
                adapter=profile_id,
                base_url=base_url,
                api_key_env=env_name,
                api_key_file=str(key_path(profile_id)),
                model=default_model
                if any(model.id == default_model for model in models)
                else models[0].id,
                reasoning_effort="" if profile_id in {"openai", "xai"} else "high",
                supported_reasoning_efforts=[]
                if profile_id in {"openai", "xai"}
                else ["low", "high", "max"],
                timeout_seconds=600,
            )
            managed: dict[str, object] = (
                json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
            )
            managed[profile_id] = profile.model_dump()
            # Metadata contains only a path. A failed key write remains unconnected.
            atomic_private_write(metadata_path, json.dumps(managed, indent=2) + "\n")
            atomic_private_write(key_path(profile_id), key + "\n")
            if profile_id not in state.providers:
                state.providers[profile_id] = ScheduledProvider(
                    provider_class(
                        base_url,
                        profile_id=profile_id,
                        api_key_env=env_name,
                        api_key_file=str(key_path(profile_id)),
                        default_model=profile.model,
                    )
                )
            state.config.providers[profile_id] = profile
            results[profile_id] = model_result(models)
            return rows()

    @app.delete("/v1/connections/{profile_id}", dependencies=auth)
    async def disconnect(profile_id: str) -> list[dict[str, object]]:
        async with lock:
            row = next((row for row in rows() if row["id"] == profile_id), None)
            if not row or not row["can_disconnect"]:
                raise ApiError(
                    409,
                    "connection_managed_externally",
                    "This connection is managed outside Settings.",
                )
            if busy():
                raise ApiError(
                    409, "provider_busy", "Wait for active work to finish before disconnecting."
                )
            key_path(profile_id).unlink(missing_ok=True)
            results[profile_id] = {"status": "not_connected", "models": []}
            return rows()
