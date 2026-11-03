import logging
import time
from collections.abc import Callable, Sequence

from app.integrations.ai.base import (
    AllProvidersFailedError,
    CompletionRequest,
    CompletionResult,
    LLMProvider,
    ProviderError,
)

logger = logging.getLogger(__name__)


class FallbackChain:
    """Tries providers in order, skipping any that recently failed.

    A failing provider is put on cooldown so that an exhausted free-tier quota
    is not hammered on every message. The chain is itself an `LLMProvider`.
    """

    name = "fallback"

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not providers:
            raise ValueError("FallbackChain needs at least one provider")
        self._providers = tuple(providers)
        self._clock = clock
        self._cooldown_until: dict[str, float] = {}

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        failures: list[str] = []
        for provider in self._providers:
            if self._cooldown_until.get(provider.name, 0.0) > self._clock():
                failures.append(f"{provider.name}: cooling down")
                continue
            try:
                result = await provider.complete(request)
            except ProviderError as exc:
                self._cooldown_until[provider.name] = self._clock() + exc.cooldown_seconds
                logger.warning(
                    "LLM provider failed",
                    extra={
                        "provider": provider.name,
                        "error": type(exc).__name__,
                        "cooldown_seconds": exc.cooldown_seconds,
                    },
                )
                failures.append(f"{provider.name}: {type(exc).__name__}")
                continue
            self._cooldown_until.pop(provider.name, None)
            return result
        raise AllProvidersFailedError(failures)
