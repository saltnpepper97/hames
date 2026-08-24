from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hames import __version__
from hames.agent import load_agent
from hames.cli import main
from hames.config import load_config
from hames.context import CORE_CONTRACT
from hames.doctor import run_doctor
from hames.paths import HamesPaths


def test_default_and_overridden_home(tmp_path: Path) -> None:
    expected = (tmp_path / "private").resolve()
    paths = HamesPaths.resolve(environ={"HAMES_HOME": str(expected)})
    assert paths.root == expected
    assert paths.database == expected / "hames.db"
    assert paths.skills == expected / "skills"


def test_core_contract_keeps_model_and_harness_authority_separate() -> None:
    assert "Use only the supplied tools" in CORE_CONTRACT
    assert "a path in context is\nnot evidence" in CORE_CONTRACT
    assert "do not describe yourself as stateless per turn" in CORE_CONTRACT
    assert "Do not claim hidden\nmemory, Skills" in CORE_CONTRACT


def test_foundation_is_private_and_does_not_overwrite(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    original = hames_paths.default_agent.read_text()
    hames_paths.ensure_foundation()
    assert hames_paths.default_agent.read_text() == original
    assert hames_paths.root.stat().st_mode & 0o777 == 0o700
    assert hames_paths.default_agent.stat().st_mode & 0o777 == 0o600
    assert hames_paths.gateway_token.stat().st_mode & 0o777 == 0o600
    assert load_agent(hames_paths.default_agent).metadata.id == "default"


def test_strict_config_and_environment_override(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    hames_paths.config_file.write_text("[gateway]\nport = 7000\n")
    config = load_config(hames_paths, environ={"HAMES_GATEWAY__PORT": "7412"})
    assert config.gateway.port == 7412

    hames_paths.config_file.write_text("[gateway]\ntyop = 1\n")
    with pytest.raises(ValidationError, match="tyop"):
        load_config(hames_paths, environ={})


def test_nested_provider_override_keeps_default_endpoint(hames_paths: HamesPaths) -> None:
    config = load_config(
        hames_paths,
        environ={"HAMES_PROVIDERS__LLAMA_CPP__MODEL": "qwen3.8-27b"},
    )
    assert config.providers["llama_cpp"].model == "qwen3.8-27b"
    assert config.providers["llama_cpp"].base_url == "http://127.0.0.1:8080"


def test_named_provider_profiles_can_share_an_adapter(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    hames_paths.config_file.write_text(
        """\
[runtime]
default_provider = "fast"

[providers.fast]
adapter = "llama_cpp"
base_url = "http://127.0.0.1:8080"
model = "qwen3.8-27b"
reasoning_effort = "medium"
supported_reasoning_efforts = ["low", "medium", "xhigh"]

[providers.deep]
adapter = "llama_cpp"
base_url = "http://127.0.0.1:8081"
model = "qwen3.8-27b"
reasoning_effort = "xhigh"
supported_reasoning_efforts = ["low", "medium", "xhigh"]
""",
        encoding="utf-8",
    )

    config = load_config(hames_paths, environ={})

    assert config.runtime.default_provider == "fast"
    assert config.providers["fast"].adapter == "llama_cpp"
    assert config.providers["deep"].base_url == "http://127.0.0.1:8081"


def test_cloud_provider_profiles_keep_api_and_subscription_auth_separate(
    hames_paths: HamesPaths,
) -> None:
    hames_paths.ensure_foundation()
    hames_paths.config_file.write_text(
        """\
[providers.openai]
adapter = "openai"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"

[providers.codex]
adapter = "codex"
base_url = "app-server://codex"
""",
        encoding="utf-8",
    )

    config = load_config(hames_paths, environ={})

    assert config.providers["openai"].api_key_env == "OPENAI_API_KEY"
    assert config.providers["codex"].api_key_env == ""
    with pytest.raises(ValidationError, match="subscription auth does not use api_key_env"):
        config.providers["codex"].model_validate(
            {
                "adapter": "codex",
                "base_url": "app-server://codex",
                "api_key_env": "OPENAI_API_KEY",
            }
        )


def test_blob_threshold_environment_override(hames_paths: HamesPaths) -> None:
    config = load_config(
        hames_paths,
        environ={"HAMES_LEDGER__BLOB_THRESHOLD_BYTES": "131072"},
    )
    assert config.ledger.blob_threshold_bytes == 131_072


def test_agent_runtime_limits_are_configurable(hames_paths: HamesPaths) -> None:
    config = load_config(
        hames_paths,
        environ={
            "HAMES_RUNTIME__MAX_MODEL_TURNS_PER_USER_MESSAGE": "8",
            "HAMES_RUNTIME__MAX_TOOL_CALLS_PER_RUN": "20",
            "HAMES_RUNTIME__MAX_ACTIVE_SECONDS_PER_RUN": "90",
            "HAMES_TOOLS__SHELL_TIMEOUT_SECONDS": "30",
        },
    )
    assert config.runtime.max_model_turns_per_user_message == 8
    assert config.runtime.max_tool_calls_per_run == 20
    assert config.runtime.max_active_seconds_per_run == 90
    assert config.tools.shell_timeout_seconds == 30


def test_context_capacity_is_strict_and_configurable(hames_paths: HamesPaths) -> None:
    config = load_config(
        hames_paths,
        environ={
            "HAMES_CONTEXT__FALLBACK_WINDOW_TOKENS": "65536",
            "HAMES_CONTEXT__OUTPUT_RESERVE_TOKENS": "8192",
            "HAMES_PROVIDERS__LLAMA_CPP__CONTEXT_WINDOW_TOKENS": "131072",
        },
    )
    assert config.context.fallback_window_tokens == 65_536
    assert config.context.output_reserve_tokens == 8_192
    assert config.providers["llama_cpp"].context_window_tokens == 131_072


def test_legacy_config_is_translated_without_rewriting(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    legacy = """\
schema_version = 10
active_provider = "llamacpp"

[providers.llamacpp]
type = "llamacpp"
base_url = "http://127.0.0.1:8080/v1"
model = "qwen3.8-27b"
models = ["qwen3.8-27b", "gemma"]
reasoning_effort = "medium"
timeout_seconds = 600.0

[runtime]
tool_loop_limit = 999

[skills]
self_authoring = "automatic"
"""
    hames_paths.config_file.write_text(legacy, encoding="utf-8")

    config = load_config(hames_paths, environ={})

    assert config.runtime.default_provider == "llama_cpp"
    assert config.providers["llama_cpp"].base_url == "http://127.0.0.1:8080"
    assert config.providers["llama_cpp"].model == "qwen3.8-27b"
    assert config.providers["llama_cpp"].reasoning_effort == "medium"
    assert config.providers["llama_cpp"].timeout_seconds == 600.0
    assert hames_paths.config_file.read_text(encoding="utf-8") == legacy
    assert run_doctor(hames_paths).config_compatibility is not None


def test_agent_rejects_unknown_frontmatter(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    hames_paths.default_agent.write_text("---\nid: default\nname: Hames\ntyop: true\n---\nHello\n")
    with pytest.raises(ValidationError, match="tyop"):
        load_agent(hames_paths.default_agent)


def test_doctor_and_cli_json(
    hames_paths: HamesPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HAMES_HOME", str(hames_paths.root))
    report = run_doctor(hames_paths)
    assert report.healthy
    assert report.version == __version__
    assert report.sqlite_fts5

    assert main(["doctor", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hames_home"] == str(hames_paths.root)
