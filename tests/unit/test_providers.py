from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import httpx
import pytest

from hames.providers import (
    ModelRequest,
    ProviderError,
    ProviderMessage,
    StreamEventKind,
    ToolCall,
    ToolDefinition,
)
from hames.providers.codex import CodexProvider
from hames.providers.llama_cpp import LlamaCppProvider
from hames.providers.ollama import OllamaProvider
from hames.providers.openai import OpenAIProvider


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
                    'data: {"id":"one","choices":[{"delta":{},"finish_reason":"stop"}]}',
                    'data: {"id":"one","choices":[],"usage":'
                    '{"prompt_tokens":10,"completion_tokens":4,'
                    '"completion_tokens_details":{"reasoning_tokens":2},"cost":0.25}}',
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
    assert events[-2].usage is not None
    assert events[-2].usage.reasoning_tokens == 2
    assert events[-2].usage.provider_reported_cost == 0.25
    await client.aclose()


@pytest.mark.asyncio
async def test_llama_cpp_keeps_router_reasoning_levels_model_specific() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "gemma-4-26b-a4b-it",
                            "status": {
                                "value": "unloaded",
                                "args": ["llama-server", "--reasoning-budget", "6144"],
                            },
                        },
                        {"id": "qwen3.8-27b", "status": {"value": "unloaded"}},
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = LlamaCppProvider(
        "http://llama",
        default_model="qwen3.8-27b",
        supported_reasoning_efforts=["low", "medium", "xhigh"],
        client=client,
    )

    gemma, qwen = await provider.list_models()

    assert gemma.reasoning_supported is True
    assert gemma.reasoning_efforts == ["on"]
    assert qwen.reasoning_supported is True
    assert qwen.reasoning_efforts == ["low", "medium", "xhigh"]
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
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "thinking"],
                    "model_info": {"qwen3.context_length": 65_536},
                },
            )
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
    assert models[0].reasoning_efforts == ["on"]
    assert models[0].context_length == 65_536
    events = [
        event
        async for event in provider.stream(
            ModelRequest(
                model="qwen3",
                messages=[ProviderMessage(role="user", content="hello")],
                system="",
                reasoning_effort="on",
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
    assert seen_request["think"] is True
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


@pytest.mark.asyncio
async def test_llama_cpp_normalizes_streamed_tool_calls() -> None:
    seen_request: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            text="\n".join(
                [
                    'data: {"id":"one","choices":[{"delta":{"tool_calls":['
                    '{"index":0,"id":"call-1","function":{"name":"read_file",'
                    '"arguments":"{\\"pa"}}]}}]}',
                    'data: {"id":"one","choices":[{"delta":{"tool_calls":['
                    '{"index":0,"function":{"arguments":"th\\":\\"README.md\\"}"}}]},'
                    '"finish_reason":"tool_calls"}]}',
                    "data: [DONE]",
                ]
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = LlamaCppProvider("http://llama", client=client)
    events = [
        event
        async for event in provider.stream(
            ModelRequest(
                model="fixture",
                messages=[
                    ProviderMessage(role="user", content="inspect"),
                    ProviderMessage(
                        role="assistant",
                        content="",
                        reasoning_content="I should inspect it.",
                        tool_calls=[
                            ToolCall(
                                id="hames-call-1",
                                name="read_file",
                                arguments={"path": "README.md"},
                            )
                        ],
                    ),
                    ProviderMessage(
                        role="tool",
                        content='{"status":"completed"}',
                        tool_call_id="hames-call-1",
                        tool_name="read_file",
                    ),
                ],
                system="",
                tools=[
                    ToolDefinition(
                        name="read_file",
                        description="Read one file",
                        input_schema={
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    )
                ],
            )
        )
    ]

    tool_events = [event for event in events if event.kind is StreamEventKind.TOOL_CALL_DELTA]
    assert len(tool_events) == 2
    assert tool_events[0].tool_call is not None
    assert tool_events[0].tool_call.name == "read_file"
    assert "tools" in seen_request
    assert seen_request["parallel_tool_calls"] is False
    sent_messages = seen_request["messages"]
    assert isinstance(sent_messages, list)
    assert sent_messages[-2]["tool_calls"][0]["id"] == "hames-call-1"
    assert sent_messages[-1]["tool_call_id"] == "hames-call-1"
    assert events[-1].finish_reason == "tool_calls"
    await client.aclose()


@pytest.mark.asyncio
async def test_ollama_maps_normalized_tool_history_by_name() -> None:
    seen_request: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            text='{"message":{"content":"done"},"done":true,"done_reason":"stop"}\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaProvider("http://ollama", client=client)
    _ = [
        event
        async for event in provider.stream(
            ModelRequest(
                model="fixture",
                system="",
                messages=[
                    ProviderMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="hames-call-1",
                                name="read_file",
                                arguments={"path": "README.md"},
                            )
                        ],
                    ),
                    ProviderMessage(
                        role="tool",
                        content="fixture",
                        tool_call_id="hames-call-1",
                        tool_name="read_file",
                    ),
                ],
            )
        )
    ]
    sent_messages = seen_request["messages"]
    assert isinstance(sent_messages, list)
    assert sent_messages[0]["tool_calls"][0]["function"]["name"] == "read_file"
    assert sent_messages[1]["tool_name"] == "read_file"
    await client.aclose()


@pytest.mark.asyncio
async def test_llama_cpp_timeout_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = LlamaCppProvider("http://llama", client=client)
    with pytest.raises(ProviderError, match="fixture timeout") as raised:
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
    assert raised.value.code == "provider_timeout"
    assert raised.value.retryable is True
    await client.aclose()


@pytest.mark.asyncio
async def test_ollama_rejects_data_after_completed_chunk() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="\n".join(
                [
                    '{"message":{"content":"done"},"done":true}',
                    '{"message":{"content":"late"},"done":false}',
                ]
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaProvider("http://ollama", client=client)
    with pytest.raises(ProviderError, match="after its completed chunk") as raised:
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
    assert raised.value.code == "malformed_provider_event"
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_responses_stream_preserves_encrypted_reasoning_state() -> None:
    seen_request: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer fixture-key"
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "gpt-5.4"}, {"id": "text-embedding-3-small"}]},
            )
        if request.url.path == "/v1/responses":
            seen_request.update(json.loads(request.content))
            return httpx.Response(
                200,
                text="\n".join(
                    [
                        'data: {"type":"response.created","response":{"id":"resp-1"}}',
                        'data: {"type":"response.reasoning_summary_text.delta","delta":"check "}',
                        'data: {"type":"response.output_text.delta","delta":"done"}',
                        'data: {"type":"response.completed","response":{"usage":'
                        '{"input_tokens":8,"output_tokens":3,"input_tokens_details":'
                        '{"cached_tokens":2},"output_tokens_details":{"reasoning_tokens":1}},'
                        '"output":[{"type":"reasoning","id":"rs-1",'
                        '"encrypted_content":"opaque"}]}}',
                        "data: [DONE]",
                    ]
                ),
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(
        "https://api.openai.test/v1",
        environ={"OPENAI_API_KEY": "fixture-key"},
        supported_reasoning_efforts=["low", "medium", "high"],
        client=client,
    )
    models = await provider.list_models()
    assert [model.id for model in models] == ["gpt-5.4"]
    assert models[0].reasoning_efforts == ["low", "medium", "high"]
    events = [
        event
        async for event in provider.stream(
            ModelRequest(
                model="gpt-5.4",
                messages=[ProviderMessage(role="user", content="hello")],
                system="contract",
                reasoning_effort="medium",
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
    assert seen_request["store"] is False
    assert seen_request["include"] == ["reasoning.encrypted_content"]
    assert events[-1].provider_items[0]["encrypted_content"] == "opaque"
    await client.aclose()


def _fake_codex_app_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_codex.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            for line in sys.stdin:
                message = json.loads(line)
                request_id = message.get("id")
                method = message.get("method")
                params = message.get("params", {})
                if request_id is None:
                    continue
                if method == "initialize":
                    result = {"userAgent": "fixture"}
                elif method == "account/read":
                    result = {"account": {"type": "chatgpt"}}
                elif method == "model/list":
                    result = {"data": [{
                        "id": "fixture-id",
                        "model": "gpt-5.4-codex",
                        "hidden": False,
                        "inputModalities": ["text", "image"],
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "medium", "description": "balanced"},
                            {"reasoningEffort": "high", "description": "deep"},
                        ],
                    }], "nextCursor": None}
                elif method == "thread/start":
                    if not params.get("dynamicTools"):
                        print(json.dumps({"id": request_id, "error": {
                            "code": -1, "message": "dynamic tools missing"}}), flush=True)
                        continue
                    result = {"thread": {"id": "thread-1"}}
                elif method == "turn/start":
                    result = {"turn": {"id": "turn-1"}}
                    print(json.dumps({"id": request_id, "result": result}), flush=True)
                    prompt = params["input"][0]["text"]
                    if "USE TOOL" in prompt:
                        print(json.dumps({
                            "id": 900,
                            "method": "item/tool/call",
                            "params": {"callId": "call-1", "threadId": "thread-1",
                                       "turnId": "turn-1", "tool": "read_file",
                                       "arguments": {"path": "README.md"}},
                        }), flush=True)
                    else:
                        print(json.dumps({"method": "item/reasoning/summaryTextDelta",
                                          "params": {"delta": "consider ", "itemId": "r1",
                                                     "threadId": "thread-1", "turnId": "turn-1",
                                                     "summaryIndex": 0}}), flush=True)
                        print(json.dumps({"method": "item/agentMessage/delta",
                                          "params": {"delta": "answer", "itemId": "a1",
                                                     "threadId": "thread-1", "turnId": "turn-1"}}),
                              flush=True)
                        print(json.dumps({"method": "thread/tokenUsage/updated", "params": {
                            "threadId": "thread-1", "turnId": "turn-1", "tokenUsage": {
                                "last": {"inputTokens": 9, "outputTokens": 4,
                                         "cachedInputTokens": 2, "reasoningOutputTokens": 1,
                                         "totalTokens": 13},
                                "total": {"inputTokens": 9, "outputTokens": 4,
                                          "cachedInputTokens": 2, "reasoningOutputTokens": 1,
                                          "totalTokens": 13}}}}), flush=True)
                        print(json.dumps({"method": "turn/completed", "params": {
                            "threadId": "thread-1", "turn": {"id": "turn-1", "items": [],
                                                                  "status": "completed"}}}),
                              flush=True)
                    continue
                else:
                    result = {}
                print(json.dumps({"id": request_id, "result": result}), flush=True)
            """
        ),
        encoding="utf-8",
    )
    return script


@pytest.mark.asyncio
async def test_codex_subscription_discovers_models_and_normalizes_app_server_stream(
    tmp_path: Path,
) -> None:
    provider = CodexProvider(command=(sys.executable, str(_fake_codex_app_server(tmp_path))))
    models = await provider.list_models()
    assert [model.id for model in models] == ["gpt-5.4-codex"]
    assert models[0].input_modalities == ["text", "image"]
    assert models[0].reasoning_efforts == ["medium", "high"]

    events = [
        event
        async for event in provider.stream(
            ModelRequest(
                model="gpt-5.4-codex",
                messages=[ProviderMessage(role="user", content="ANSWER")],
                system="contract",
                reasoning_effort="medium",
                tools=[
                    ToolDefinition(
                        name="read_file",
                        description="Read one file",
                        input_schema={"type": "object"},
                    )
                ],
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
    assert events[-2].usage is not None
    assert events[-2].usage.cached_input_tokens == 2


@pytest.mark.asyncio
async def test_codex_dynamic_tools_return_to_the_hames_tool_loop(tmp_path: Path) -> None:
    provider = CodexProvider(command=(sys.executable, str(_fake_codex_app_server(tmp_path))))
    events = [
        event
        async for event in provider.stream(
            ModelRequest(
                model="gpt-5.4-codex",
                messages=[ProviderMessage(role="user", content="USE TOOL")],
                system="contract",
                tools=[
                    ToolDefinition(
                        name="read_file",
                        description="Read one file",
                        input_schema={"type": "object"},
                    )
                ],
            )
        )
    ]
    assert [event.kind for event in events] == [
        StreamEventKind.STARTED,
        StreamEventKind.TOOL_CALL_DELTA,
        StreamEventKind.COMPLETED,
    ]
    assert events[1].tool_call is not None
    assert events[1].tool_call.name == "read_file"
    assert events[-1].finish_reason == "tool_calls"
