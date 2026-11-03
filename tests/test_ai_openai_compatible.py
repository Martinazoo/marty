import json
from typing import Any

import httpx
import pytest

from app.integrations.ai import (
    ChatMessage,
    CompletionRequest,
    InvalidRequestError,
    OpenAICompatibleProvider,
    ProviderUnavailableError,
    RateLimitedError,
    Role,
    ToolCall,
    ToolSpec,
)
from app.integrations.ai.factory import build_llm, build_providers
from tests.conftest import make_settings

TOOL = ToolSpec(
    "remember",
    "Store a fact.",
    {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
)
SIGNATURE = {"extra_content": {"google": {"thought_signature": "opaque=="}}}


def completion(message: dict[str, Any], usage: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
    }


def make_provider(
    handler, name: str = "gemini", **kwargs: Any
) -> tuple[OpenAICompatibleProvider, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recorder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    provider = OpenAICompatibleProvider(
        name=name,
        base_url="https://llm.test/v1/",
        model="test-model",
        client=client,
        api_key="sk-test",
        **kwargs,
    )
    return provider, requests


def reply(status: int, body: Any = None, headers: dict[str, str] | None = None):
    return lambda request: httpx.Response(status, json=body, headers=headers)


async def test_sends_messages_and_tools_and_parses_text():
    provider, requests = make_provider(
        reply(200, completion({"role": "assistant", "content": " Hola "}))
    )

    result = await provider.complete(
        CompletionRequest(
            (ChatMessage(Role.SYSTEM, "sys"), ChatMessage(Role.USER, "hola")),
            tools=(TOOL,),
            max_output_tokens=256,
        )
    )

    request = requests[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 256
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hola"},
    ]
    assert body["tools"][0]["function"]["name"] == "remember"
    assert body["tool_choice"] == "auto"
    assert result.message.content == "Hola"
    assert (result.provider, result.model) == ("gemini", "test-model")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (10, 5)


async def test_omits_tools_when_none_are_given():
    provider, requests = make_provider(reply(200, completion({"content": "ok"})))

    await provider.complete(CompletionRequest((ChatMessage(Role.USER, "hola"),)))

    body = json.loads(requests[0].content)
    assert "tools" not in body and "tool_choice" not in body


async def test_parses_tool_calls_and_keeps_provider_fields():
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "remember", "arguments": '{"fact": "azul"}'},
                **SIGNATURE,
            }
        ],
    }
    provider, _ = make_provider(reply(200, completion(message)))

    result = await provider.complete(CompletionRequest((ChatMessage(Role.USER, "x"),), (TOOL,)))

    (call,) = result.message.tool_calls
    assert (call.id, call.name, call.arguments) == ("call_1", "remember", {"fact": "azul"})
    assert call.provider == "gemini/test-model"
    assert call.provider_fields == SIGNATURE
    assert result.message.content is None


async def test_unparseable_arguments_are_passed_on_for_the_executor_to_reject():
    message = {
        "tool_calls": [{"id": "c", "function": {"name": "remember", "arguments": "{not json"}}]
    }
    provider, _ = make_provider(reply(200, completion(message)))

    result = await provider.complete(CompletionRequest((ChatMessage(Role.USER, "x"),), (TOOL,)))

    assert result.message.tool_calls[0].arguments == {"_unparseable_arguments": "{not json"}


def history(call: ToolCall) -> CompletionRequest:
    return CompletionRequest(
        (
            ChatMessage(Role.USER, "x"),
            ChatMessage(Role.ASSISTANT, None, tool_calls=(call,)),
            ChatMessage(Role.TOOL, '{"ok": true}', tool_call_id=call.id),
        ),
        (TOOL,),
    )


async def test_echoes_own_provider_fields_in_history():
    provider, requests = make_provider(reply(200, completion({"content": "ok"})))
    call = ToolCall("c1", "remember", {"fact": "azul"}, "gemini/test-model", SIGNATURE)

    await provider.complete(history(call))

    messages = json.loads(requests[0].content)["messages"]
    assert messages[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "remember", "arguments": '{"fact": "azul"}'},
                **SIGNATURE,
            }
        ],
    }
    assert messages[2] == {"role": "tool", "content": '{"ok": true}', "tool_call_id": "c1"}


async def test_foreign_tool_calls_get_placeholder_fields_not_the_other_providers():
    placeholder = {"extra_content": {"google": {"thought_signature": "placeholder"}}}
    provider, requests = make_provider(
        reply(200, completion({"content": "ok"})), foreign_tool_call_fields=placeholder
    )
    call = ToolCall("c1", "remember", {}, "groq/test-model", {"groq_only": True})

    await provider.complete(history(call))

    encoded = json.loads(requests[0].content)["messages"][1]["tool_calls"][0]
    assert encoded["extra_content"] == placeholder["extra_content"]
    assert "groq_only" not in encoded


async def test_signatures_of_another_model_of_the_same_provider_are_not_echoed():
    placeholder = {"extra_content": {"google": {"thought_signature": "placeholder"}}}
    provider, requests = make_provider(
        reply(200, completion({"content": "ok"})), foreign_tool_call_fields=placeholder
    )

    await provider.complete(
        history(ToolCall("c1", "remember", {}, "gemini/other-model", SIGNATURE))
    )

    encoded = json.loads(requests[0].content)["messages"][1]["tool_calls"][0]
    assert encoded["extra_content"] == placeholder["extra_content"]


async def test_other_providers_fields_are_dropped_without_placeholder():
    provider, requests = make_provider(reply(200, completion({"content": "ok"})), name="groq")

    await provider.complete(history(ToolCall("c1", "remember", {}, "gemini/test-model", SIGNATURE)))

    encoded = json.loads(requests[0].content)["messages"][1]["tool_calls"][0]
    assert "extra_content" not in encoded


async def test_empty_answer_is_a_provider_failure_without_cooldown():
    provider, _ = make_provider(reply(200, completion({"content": "   "})))

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await provider.complete(CompletionRequest((ChatMessage(Role.USER, "x"),)))
    assert excinfo.value.cooldown_seconds == 0


@pytest.mark.parametrize(
    ("status", "body", "headers", "error", "cooldown"),
    [
        (429, {"error": {"message": "slow down"}}, {"retry-after": "12"}, RateLimitedError, 12),
        (
            429,
            [
                {
                    "error": {
                        "code": 429,
                        "status": "RESOURCE_EXHAUSTED",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                "retryDelay": "34s",
                            }
                        ],
                    }
                }
            ],
            None,
            RateLimitedError,
            34,
        ),
        (429, None, None, RateLimitedError, 60),
        (413, {"error": {"code": "rate_limit_exceeded"}}, None, RateLimitedError, 60),
        (401, {"error": {"message": "Invalid API Key"}}, None, ProviderUnavailableError, 900),
        (
            400,
            [
                {
                    "error": {
                        "code": 400,
                        "message": "API key not valid",
                        "status": "INVALID_ARGUMENT",
                    }
                }
            ],
            None,
            ProviderUnavailableError,
            900,
        ),
        (404, {"error": {"message": "model not found"}}, None, ProviderUnavailableError, 900),
        (503, [{"error": {"status": "UNAVAILABLE"}}], None, ProviderUnavailableError, 30),
        (400, {"error": {"code": "tool_use_failed"}}, None, ProviderUnavailableError, 0),
    ],
)
async def test_maps_http_errors(status, body, headers, error, cooldown):
    provider, _ = make_provider(reply(status, body, headers))

    with pytest.raises(error) as excinfo:
        await provider.complete(CompletionRequest((ChatMessage(Role.USER, "x"),)))
    assert excinfo.value.cooldown_seconds == cooldown


async def test_bad_request_is_our_bug_and_not_retried():
    provider, _ = make_provider(reply(400, {"error": {"message": "messages: field required"}}))

    with pytest.raises(InvalidRequestError, match="field required"):
        await provider.complete(CompletionRequest((ChatMessage(Role.USER, "x"),)))


async def test_timeouts_and_network_errors_are_unavailability():
    def raise_timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def raise_connect(request):
        raise httpx.ConnectError("down", request=request)

    for handler in (raise_timeout, raise_connect):
        provider, _ = make_provider(handler)
        with pytest.raises(ProviderUnavailableError):
            await provider.complete(CompletionRequest((ChatMessage(Role.USER, "x"),)))


async def test_list_models_strips_google_prefix():
    body = {"data": [{"id": "models/gemini-b"}, {"id": "gemini-a"}]}
    provider, requests = make_provider(reply(200, body))

    assert await provider.list_models() == ["gemini-a", "gemini-b"]
    assert str(requests[0].url) == "https://llm.test/v1/models"


async def test_factory_uses_configured_providers_in_order():
    settings = make_settings(
        ai_provider_order="groq,gemini,ollama",
        gemini_api_key="g-key",
        gemini_model="gemini-x, gemini-x-lite",
        groq_api_key="gsk-key",
        groq_model="",  # incomplete: skipped
        ollama_model="llama-local",
    )
    async with httpx.AsyncClient() as client:
        providers = build_providers(settings, client)

        assert [(p.name, p.model) for p in providers] == [
            ("gemini", "gemini-x"),
            ("gemini", "gemini-x-lite"),
            ("ollama", "llama-local"),
        ]
        assert build_llm(make_settings(), client) is None
