"""Strict Hames TOML configuration."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hames.paths import HamesPaths


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeConfig(StrictModel):
    default_agent: str = "default"
    default_provider: str = "llama_cpp"


class GatewayConfig(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=7411, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("M0 gateway host must be loopback")
        return value


class ProviderConfig(StrictModel):
    base_url: str
    model: str = ""
    reasoning_effort: str = ""
    timeout_seconds: float = Field(default=120.0, gt=0)


class ProvidersConfig(StrictModel):
    llama_cpp: ProviderConfig = ProviderConfig(base_url="http://127.0.0.1:8080")
    ollama: ProviderConfig = ProviderConfig(base_url="http://127.0.0.1:11434")


class LoggingConfig(StrictModel):
    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def known_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unknown logging level")
        return normalized


class ReplConfig(StrictModel):
    show_reasoning: bool = True


class HamesConfig(StrictModel):
    runtime: RuntimeConfig = RuntimeConfig()
    gateway: GatewayConfig = GatewayConfig()
    providers: ProvidersConfig = ProvidersConfig()
    logging: LoggingConfig = LoggingConfig()
    repl: ReplConfig = ReplConfig()


def _environment_overrides(environ: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, raw_value in environ.items():
        if not key.startswith("HAMES_") or key == "HAMES_HOME":
            continue
        path = key.removeprefix("HAMES_").lower().split("__")
        if len(path) < 2:
            continue
        target = result
        for part in path[:-1]:
            target = target.setdefault(part, {})
        try:
            value: Any = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        target[path[-1]] = value
    return result


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)  # type: ignore[index]
        else:
            base[key] = value
    return base


def load_config(
    paths: HamesPaths,
    *,
    environ: Mapping[str, str] | None = None,
) -> HamesConfig:
    env = os.environ if environ is None else environ
    source = HamesConfig().model_dump()
    if paths.config_file.exists():
        with paths.config_file.open("rb") as handle:
            file_config = tomllib.load(handle)
        if is_legacy_config(file_config):
            _deep_merge(source, _translate_legacy_config(file_config))
        else:
            _deep_merge(source, file_config)
    merged = _deep_merge(source, _environment_overrides(env))
    return HamesConfig.model_validate(merged)


def is_legacy_config(value: Mapping[str, Any]) -> bool:
    """Recognize the pre-rewrite config without weakening strict M0 parsing."""

    return "schema_version" in value and "active_provider" in value


def _translate_legacy_config(value: Mapping[str, Any]) -> dict[str, Any]:
    """Map the safe M0 subset of a legacy config without changing the file."""

    provider_aliases = {"llamacpp": "llama_cpp", "llama_cpp": "llama_cpp", "ollama": "ollama"}
    active = value.get("active_provider")
    selected = provider_aliases.get(active) if isinstance(active, str) else None
    translated: dict[str, Any] = {}
    if selected is not None:
        translated["runtime"] = {"default_provider": selected}

    raw_providers = value.get("providers")
    if not isinstance(raw_providers, Mapping):
        return translated
    provider_values = cast(Mapping[str, Any], raw_providers)
    providers: dict[str, Any] = {}
    for legacy_name, current_name in (
        ("llamacpp", "llama_cpp"),
        ("llama_cpp", "llama_cpp"),
        ("ollama", "ollama"),
    ):
        raw_provider = provider_values.get(legacy_name)
        if not isinstance(raw_provider, Mapping):
            continue
        provider_value = cast(Mapping[str, Any], raw_provider)
        mapped: dict[str, Any] = {
            key: provider_value[key]
            for key in ("base_url", "model", "reasoning_effort", "timeout_seconds")
            if key in provider_value
        }
        base_url = mapped.get("base_url")
        if current_name == "llama_cpp" and isinstance(base_url, str):
            mapped["base_url"] = base_url.rstrip("/").removesuffix("/v1")
        providers[current_name] = mapped
    if providers:
        translated["providers"] = providers
    return translated


def configured_database_path(paths: HamesPaths, configured: str = "") -> Path:
    if not configured:
        return paths.database
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        candidate = paths.root / candidate
    return candidate.resolve(strict=False)
