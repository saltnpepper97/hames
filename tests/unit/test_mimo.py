from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from hames.providers.base import ModelRequest, ProviderError, ProviderMessage, ToolCall
from hames.providers.mimo import (
    MIMO_API_URL,
    MIMO_TOKEN_PLAN_URL,
    MimoProvider,
    MimoTokenPlanProvider,
)


@pytest.mark.parametrize("provider_type", [MimoProvider, MimoTokenPlanProvider])
async def test_mimo_discovery_capabilities_and_credential_precedence(
    provider_type: type[MimoProvider],
    tmp_path: Path,
) -> None:
    key = tmp_path / "key"
    key.write_text("file-secret\n")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "mimo-v2.5-pro"},
                    {"id": "mimo-v2.5"},
                    {"id": "mimo-v2.6-pro", "context_length": 999999},
                    {"id": "mimo-v2.6-flash"},
                    {"id": "mimo-v2.6-pro-ultraspeed"},
                    {"id": "mimo-v2.5-asr"},
                    {"id": "mimo-v2.5-tts-voiceclone"},
                    {"id": "unrelated"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = provider_type(client=client, api_key_file=str(key), environ={})
        models = {model.id: model for model in await provider.list_models()}
        assert len(models) == 5
        assert models["mimo-v2.5-pro"].input_modalities == ["text"]
        assert models["mimo-v2.5"].input_modalities == ["text", "image"]
        assert models["mimo-v2.6-pro"].input_modalities == ["text", "image"]
        assert models["mimo-v2.6-pro"].context_length == 999999
        assert models["mimo-v2.5-pro"].context_length == 1048576
        assert all(model.reasoning_efforts == ["on"] for model in models.values())
        assert all(model.status == "available" for model in models.values())
        assert requests[0].headers["authorization"] == "Bearer file-secret"
        expected_url = MIMO_API_URL if provider_type == MimoProvider else MIMO_TOKEN_PLAN_URL
        assert str(requests[0].url) == expected_url + "/models"
        provider = provider_type(
            client=client, api_key_file=str(key), environ={provider.api_key_env: "env-secret"}
        )
        await provider.list_models()
        assert requests[-1].headers["authorization"] == "Bearer env-secret"


@pytest.mark.parametrize("effort", ["", "on", "off", "high"])
async def test_mimo_wire_body_and_reasoning_replay(effort: str) -> None:
    async with httpx.AsyncClient() as client:
        provider = MimoProvider(client=client)
        body = provider.request_body(
            ModelRequest(
                model="mimo-v2.6-pro",
                system="instructions",
                reasoning_effort=effort,
                max_tokens=2048,
                temperature=0.4,
                messages=[
                    ProviderMessage(
                        role="assistant",
                        content="",
                        reasoning_content=" exact\nthought ",
                        tool_calls=[ToolCall(id="call", name="read_file", arguments={"path": "a"})],
                    ),
                    ProviderMessage(role="tool", content="result", tool_call_id="call"),
                ],
            )
        )
    assert body["max_completion_tokens"] == 2048
    assert "max_tokens" not in body and "stream_options" not in body
    assert "reasoning_effort" not in body
    assert body["thinking"] == {"type": "disabled" if effort == "off" else "enabled"}
    assert ("temperature" in body) == (effort == "off")
    messages = json.loads(json.dumps(body["messages"]))
    assert messages[1]["reasoning_content"] == " exact\nthought "
    assert messages[1]["tool_calls"][0]["id"] == messages[2]["tool_call_id"]


@pytest.mark.parametrize("provider_type", [MimoProvider, MimoTokenPlanProvider])
@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
async def test_mimo_errors_never_fall_back_to_another_endpoint(
    provider_type: type[MimoProvider],
    status: int,
) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = provider_type(
            client=client, environ={provider_type.default_api_key_env: "secret"}
        )
        with pytest.raises(ProviderError):
            await provider.list_models()
    assert urls == [provider.default_base_url + "/models"]
