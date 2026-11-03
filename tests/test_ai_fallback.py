import pytest

from app.integrations.ai import (
    AllProvidersFailedError,
    ChatMessage,
    CompletionRequest,
    CompletionResult,
    FallbackChain,
    InvalidRequestError,
    ProviderUnavailableError,
    RateLimitedError,
    Role,
)

REQUEST = CompletionRequest(messages=(ChatMessage(Role.USER, "hola"),))


class FakeProvider:
    def __init__(self, name: str, *outcomes: Exception | str) -> None:
        self.name = name
        self._outcomes = list(outcomes)
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return CompletionResult(ChatMessage(Role.ASSISTANT, outcome), self.name, "fake-model")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_uses_first_healthy_provider():
    primary = FakeProvider("gemini", "respuesta")
    secondary = FakeProvider("groq", "otra")

    result = await FallbackChain([primary, secondary]).complete(REQUEST)

    assert result.provider == "gemini"
    assert secondary.calls == 0


async def test_falls_back_when_rate_limited_and_skips_during_cooldown():
    clock = FakeClock()
    primary = FakeProvider("gemini", RateLimitedError("429", cooldown_seconds=60), "recovered")
    secondary = FakeProvider("groq", "uno", "dos")
    chain = FallbackChain([primary, secondary], clock=clock)

    assert (await chain.complete(REQUEST)).provider == "groq"
    clock.now += 30
    assert (await chain.complete(REQUEST)).provider == "groq"
    assert primary.calls == 1

    clock.now += 31
    assert (await chain.complete(REQUEST)).provider == "gemini"


async def test_raises_when_every_provider_fails():
    chain = FallbackChain(
        [
            FakeProvider("gemini", RateLimitedError("quota")),
            FakeProvider("groq", ProviderUnavailableError("timeout")),
        ]
    )

    with pytest.raises(AllProvidersFailedError) as excinfo:
        await chain.complete(REQUEST)

    assert excinfo.value.failures == ["gemini: RateLimitedError", "groq: ProviderUnavailableError"]


async def test_invalid_request_is_not_retried_elsewhere():
    secondary = FakeProvider("groq", "never")
    chain = FallbackChain([FakeProvider("gemini", InvalidRequestError("bad schema")), secondary])

    with pytest.raises(InvalidRequestError):
        await chain.complete(REQUEST)
    assert secondary.calls == 0


def test_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        FallbackChain([])
