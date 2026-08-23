from __future__ import annotations

import json

import httpx
import pytest

from hames.providers import ModelRequest, ProviderMessage, StreamEventKind
from hames.providers.llama_cpp import LlamaCppProvider
from hames.providers.ollama import OllamaProvider


@pytest.mark.asyncio
async def test_llama_cpp_discovers_reasoning_and_streams_separate_channels() -> None:
    seen_request: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen3.8-27b",
                            "status": {"value": "sleeping"},
                            "meta": {"n_ctx": 131072, "n_params": 27_320_697_856, "ftype": "Q4_K"},
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["text"],
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/props":
            assert request.url.params["model"] == "qwen3.8-27b"
            return httpx.Response(
                200,
                json={"chat_template_caps": {"supports_reasoning_effort": True}},
            )
        if request.url.path == "/v1/chat/completions":
            seen_request.update(json.loads(request.content))
            stream = "\n".join(
                [
                    'data: {"id":"one","choices":[{"delta":{"reasoning_content":"think "}}]}',
                    'data: {"id":"one","choices":[{"delta":{"content":"answer"}}]}',
                    'data: {"id":"one","choices":[],"usage":'
                    '{"prompt_tokens":10,"completion_tokens":4}}',
                    'data: {"id":"one","choices":[{"delta":{},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ]
            )
            return httpx.Response(200, text=stream)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = LlamaCppProvider("http://llama", client=client)
    models = await provider.list_models()
    assert models[0].reasoning_supported
    assert models[0].reasoning_efforts == ["low", "medium", "xhigh"]
    request = ModelRequest(
        model="qwen3.8-27b",
        messages=[ProviderMessage(role="user", content="hello")],
        system="contract",
        reasoning_effort="medium",
    )
    events = [event async for event in provider.stream(request)]
    assert [event.kind for event in events] == [
        StreamEventKind.STARTED,
        StreamEventKind.REASONING_DELTA,
        StreamEventKind.TEXT_DELTA,
        StreamEventKind.USAGE,
        StreamEventKind.COMPLETED,
    ]
    assert seen_request["chat_template_kwargs"] == {
        "enable_thinking": True,
        "reasoning_effort": "medium",
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_ollama_discovers_capabilities_and_streams_usage() -> None:
    seen_request: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "model": "qwen3",
                            "details": {"parameter_size": "8B", "quantization_level": "Q4_K_M"},
                        }
                    ]
                },
            )
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "thinking"]})
        if request.url.path == "/api/chat":
            seen_request.update(json.loads(request.content))
            return httpx.Response(
                200,
                text="\n".join(
                    [
                        '{"message":{"thinking":"hmm ","content":""},"done":false}',
                        '{"message":{"thinking":"","content":"yes"},"done":false}',
                        '{"message":{"content":""},"done":true,"done_reason":"stop","prompt_eval_count":5,"eval_count":2}',
                    ]
                ),
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaProvider("http://ollama", client=client)
    models = await provider.list_models()
    assert models[0].reasoning_supported
    events = [
        event
        async for event in provider.stream(
            ModelRequest(
                model="qwen3",
                messages=[ProviderMessage(role="user", content="hello")],
                system="",
                reasoning_effort="low",
            )
        )
    ]
    assert [event.kind for event in events] == [
        StreamEventKind.STARTED,
        StreamEventKind.REASONING_DELTA,
        StreamEventKind.TEXT_DELTA,
        StreamEventKind.USAGE,
        StreamEventKind.COMPLETED,
    ]
    assert seen_request["think"] == "low"
    await client.aclose()


@pytest.mark.asyncio
async def test_llama_cpp_rejects_malformed_stream() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data: definitely-not-json\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = LlamaCppProvider("http://llama", client=client)
    with pytest.raises(RuntimeError, match="invalid SSE JSON"):
        _ = [
            event
            async for event in provider.stream(
                ModelRequest(
                    model="fixture",
                    messages=[ProviderMessage(role="user", content="hello")],
                    system="",
                )
            )
        ]
    await client.aclose()


@pytest.mark.asyncio
async def test_llama_cpp_does_not_wake_unloaded_models_for_capabilities() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={"data": [{"id": "cold-model", "status": {"value": "unloaded"}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    models = await LlamaCppProvider("http://llama", client=client).list_models()
    assert models[0].reasoning_supported is None
    assert paths == ["/v1/models"]
    await client.aclose()
