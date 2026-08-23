"""Strict Hames TOML configuration."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
            _deep_merge(source, tomllib.load(handle))
    merged = _deep_merge(source, _environment_overrides(env))
    return HamesConfig.model_validate(merged)


def configured_database_path(paths: HamesPaths, configured: str = "") -> Path:
    if not configured:
        return paths.database
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        candidate = paths.root / candidate
    return candidate.resolve(strict=False)
