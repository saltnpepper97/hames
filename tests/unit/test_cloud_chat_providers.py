from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from hames.config import HamesConfig, ProviderProfileConfig, RuntimeConfig
from hames.providers.base import (
    ModelRequest,
    ProviderError,
    ProviderMessage,
    StreamEventKind,
    ToolCall,
)
from hames.providers.deepseek import DeepSeekProvider
from hames.providers.registry import configured_providers
from hames.providers.zai import ZAI_CODING_URL, ZaiCodingProvider, ZaiProvider


def sse(*chunks: dict[str, object], done: bool = True) -> httpx.Response:
    text = "\n\n".join("data: " + json.dumps(chunk) for chunk in chunks)
    return httpx.Response(200, text=text + ("\n\ndata: [DONE]\n\n" if done else "\n\n"))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [DeepSeekProvider, ZaiProvider, ZaiCodingProvider])
async def test_stream_tool_fragments_reasoning_and_final_usage(
    provider_type: type[DeepSeekProvider] | type[ZaiProvider],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return sse(
            {"id": "req-1", "choices": [{"index": 0, "delta": {"reasoning_content": " step "}}]},
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "content": "checking",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "function": {"name": "read_file", "arguments": '{"path":'},
                                }
                            ],
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": '"README.md"}'}}]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_cache_hit_tokens": 40,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = provider_type(
            client=client, environ={"DEEPSEEK_API_KEY": "secret", "ZAI_API_KEY": "secret"}
        )
        events = [
            event
            async for event in provider.stream(
                ModelRequest(
                    model="glm-5.3" if provider_type != DeepSeekProvider else "deepseek-flash",
                    system="instructions",
                    messages=[ProviderMessage(role="user", content="inspect")],
                    reasoning_effort="high",
                )
            )
        ]
    assert events[0].kind == StreamEventKind.STARTED
    assert events[1].text == " step "
    calls = [event.tool_call for event in events if event.tool_call]
    assert "".join(call.arguments_delta for call in calls) == '{"path":"README.md"}'
    assert calls[0].provider_call_id == "call-1"
    assert calls[1].name is None
    assert events[-2].kind == StreamEventKind.USAGE
    assert events[-2].usage is not None and events[-2].usage.cached_input_tokens == 40
    assert events[-1].kind == StreamEventKind.COMPLETED
    assert events[-1].finish_reason == "tool_calls"
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert requests[0].url.path.endswith("/chat/completions")
    if provider_type == ZaiCodingProvider:
        assert str(requests[0].url) == ZAI_CODING_URL + "/chat/completions"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [DeepSeekProvider, ZaiProvider])
async def test_body_preserves_assistant_reasoning_and_tool_pair(
    provider_type: type[DeepSeekProvider] | type[ZaiProvider],
) -> None:
    async with httpx.AsyncClient() as client:
        provider = provider_type(client=client)
        body = provider.request_body(
            ModelRequest(
                model="deepseek-flash" if provider_type == DeepSeekProvider else "glm-5.3",
                system="system",
                reasoning_effort="off",
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
    messages = body["messages"]
    assert isinstance(messages, list)
    assert messages[1]["reasoning_content"] == " exact\nthought "
    assert messages[1]["tool_calls"][0]["id"] == messages[2]["tool_call_id"]
    assert body["thinking"] == (
        {"type": "disabled"}
        if provider_type == DeepSeekProvider
        else {"type": "enabled", "clear_thinking": False}
    )
    if provider_type == ZaiProvider:
        assert body["reasoning_effort"] == "low"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_discovery_never_masks_auth_or_quota_failure_with_catalog(status: int) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status))
    ) as client:
        provider = ZaiCodingProvider(client=client, environ={"ZAI_API_KEY": "secret"})
        with pytest.raises(ProviderError) as caught:
            await provider.list_models()
        assert caught.value.retryable == (status in {429, 503})


@pytest.mark.asyncio
async def test_discovery_uses_only_live_ids_and_marks_unsupported_catalog() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"id": "glm-5.3", "context_length": 999999}, {"id": "glm-image"}]},
            )
        )
    ) as client:
        provider = ZaiProvider(
            client=client, environ={"ZAI_API_KEY": "secret"}, default_model="glm-not-listed"
        )
        models = await provider.list_models()
        assert [model.id for model in models] == ["glm-5.3"]
        assert models[0].context_length == 999999
        assert models[0].status == "available"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as client:
        provider = ZaiCodingProvider(client=client, environ={"ZAI_API_KEY": "secret"})
        models = await provider.list_models()
        assert [model.id for model in models] == ["glm-5.3", "glm-5.3-flash"]
        assert all(model.status == "configured" for model in models)


@pytest.mark.asyncio
async def test_truncated_stream_fails_without_completed_event() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: sse(
                {"id": "req", "choices": [{"index": 0, "delta": {"content": "partial"}}]},
                done=False,
            )
        )
    ) as client:
        provider = DeepSeekProvider(client=client, environ={"DEEPSEEK_API_KEY": "secret"})
        events: list[StreamEventKind] = []
        with pytest.raises(ProviderError, match="before completion"):
            async for event in provider.stream(
                ModelRequest(model="deepseek-flash", system="", messages=[])
            ):
                events.append(event.kind)
        assert StreamEventKind.COMPLETED not in events


@pytest.mark.asyncio
async def test_saved_credential_used_without_exposing_it_and_env_takes_precedence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "deepseek.key"
    path.write_text("local-secret\n")
    headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers["authorization"])
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider(client=client, api_key_file=str(path), environ={})
        assert (await provider.list_models())[0].context_length == 1_000_000
        provider = DeepSeekProvider(
            client=client, api_key_file=str(path), environ={"DEEPSEEK_API_KEY": "override"}
        )
        await provider.list_models()
        missing = DeepSeekProvider(client=client, api_key_file=str(tmp_path / "absent"), environ={})
        with pytest.raises(ProviderError, match="hames setup deepseek"):
            await missing.list_models()
    assert headers == ["Bearer local-secret", "Bearer override"]


@pytest.mark.asyncio
async def test_registry_supports_distinct_api_and_coding_profiles() -> None:
    config = HamesConfig(
        runtime=RuntimeConfig(default_provider="deepseek"),
        providers={
            "deepseek": ProviderProfileConfig(
                adapter="deepseek", base_url="https://api.deepseek.com"
            ),
            "zai": ProviderProfileConfig(adapter="zai", base_url="https://api.z.ai/api/paas/v4"),
            "zai_coding": ProviderProfileConfig(adapter="zai_coding", base_url=ZAI_CODING_URL),
        },
    )
    providers = configured_providers(config)
    try:
        assert isinstance(providers["deepseek"], DeepSeekProvider)
        assert isinstance(providers["zai_coding"], ZaiCodingProvider)
        assert providers["zai"].base_url != providers["zai_coding"].base_url
        assert config.providers["deepseek"].api_key_env == "DEEPSEEK_API_KEY"
        assert config.providers["zai_coding"].api_key_env == "ZAI_API_KEY"
    finally:
        for provider in providers.values():
            await provider.aclose()
