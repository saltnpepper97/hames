"""Strict Hames TOML configuration."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hames.paths import HamesPaths


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeConfig(StrictModel):
    default_agent: str = "default"
    default_provider: str = "llama_cpp"
    max_model_turns_per_user_message: int = Field(default=24, ge=1)
    max_tool_calls_per_run: int = Field(default=96, ge=1)
    max_active_seconds_per_run: float = Field(default=1800.0, gt=0)
    max_delegation_depth: int = Field(default=1, ge=0, le=2)
    max_child_runs_per_parent_run: int = Field(default=4, ge=1, le=16)


class ContextConfig(StrictModel):
    fallback_window_tokens: int = Field(default=32_768, ge=8_192)
    output_reserve_tokens: int = Field(default=4_096, ge=256)
    stable_instruction_limit_tokens: int = Field(default=8_192, ge=1_024)
    agent_identity_limit_tokens: int = Field(default=4_096, ge=512)
    tool_schema_limit_tokens: int = Field(default=8_192, ge=1_024)
    retrieved_context_limit_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def reserve_fits_fallback(self) -> ContextConfig:
        if self.output_reserve_tokens >= self.fallback_window_tokens:
            raise ValueError("output_reserve_tokens must be smaller than fallback_window_tokens")
        return self


class ToolsConfig(StrictModel):
    shell_timeout_seconds: float = Field(default=120.0, gt=0)
    shell_max_timeout_seconds: float = Field(default=600.0, gt=0)
    model_result_char_limit: int = Field(default=32_000, ge=1024)
    capture_byte_limit: int = Field(default=16_777_216, ge=65_536)
    read_byte_limit: int = Field(default=1_048_576, ge=4096)
    list_entry_limit: int = Field(default=1000, ge=1)

    @model_validator(mode="after")
    def valid_shell_timeouts(self) -> ToolsConfig:
        if self.shell_timeout_seconds > self.shell_max_timeout_seconds:
            raise ValueError("shell_timeout_seconds exceeds shell_max_timeout_seconds")
        return self


class GatewayConfig(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=7411, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("M0 gateway host must be loopback")
        return value


class ProviderProfileConfig(StrictModel):
    adapter: str
    base_url: str
    model: str = ""
    reasoning_effort: str = ""
    supported_reasoning_efforts: list[str] = Field(default_factory=list)
    context_window_tokens: int | None = Field(default=None, ge=8_192)
    timeout_seconds: float = Field(default=120.0, gt=0)

    @field_validator("adapter")
    @classmethod
    def known_adapter(cls, value: str) -> str:
        if value not in {"llama_cpp", "ollama"}:
            raise ValueError(f"unknown provider adapter: {value}")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_http_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("provider base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("provider base_url must not contain credentials")
        return value.rstrip("/")

    @field_validator("supported_reasoning_efforts")
    @classmethod
    def valid_reasoning_efforts(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("supported reasoning efforts must be unique")
        if any(not value or value == "off" for value in values):
            raise ValueError("supported reasoning efforts must not contain empty or off")
        return values

    @model_validator(mode="after")
    def default_effort_is_supported(self) -> ProviderProfileConfig:
        if (
            self.reasoning_effort
            and self.reasoning_effort != "off"
            and self.supported_reasoning_efforts
            and self.reasoning_effort not in self.supported_reasoning_efforts
        ):
            raise ValueError("reasoning_effort is not listed in supported_reasoning_efforts")
        return self


def _default_provider_profiles() -> dict[str, ProviderProfileConfig]:
    return {
        "llama_cpp": ProviderProfileConfig(adapter="llama_cpp", base_url="http://127.0.0.1:8080"),
        "ollama": ProviderProfileConfig(adapter="ollama", base_url="http://127.0.0.1:11434"),
    }


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


class LedgerConfig(StrictModel):
    blob_threshold_bytes: int = Field(default=65_536, ge=1024)


class HamesConfig(StrictModel):
    runtime: RuntimeConfig = RuntimeConfig()
    context: ContextConfig = ContextConfig()
    tools: ToolsConfig = ToolsConfig()
    gateway: GatewayConfig = GatewayConfig()
    providers: dict[str, ProviderProfileConfig] = Field(default_factory=_default_provider_profiles)
    logging: LoggingConfig = LoggingConfig()
    repl: ReplConfig = ReplConfig()
    ledger: LedgerConfig = LedgerConfig()

    @model_validator(mode="after")
    def valid_provider_profiles(self) -> HamesConfig:
        import re

        invalid = [
            profile_id
            for profile_id in self.providers
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", profile_id) is None
        ]
        if invalid:
            raise ValueError(f"invalid provider profile id: {invalid[0]}")
        if self.runtime.default_provider not in self.providers:
            raise ValueError(
                f"default provider profile is not configured: {self.runtime.default_provider}"
            )
        return self


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
            for key in (
                "base_url",
                "model",
                "reasoning_effort",
                "supported_reasoning_efforts",
                "context_window_tokens",
                "timeout_seconds",
            )
            if key in provider_value
        }
        mapped["adapter"] = current_name
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
