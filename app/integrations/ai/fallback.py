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
    model = "any"

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
            # Quotas are per model, so one provider can appear with several models.
            key = f"{provider.name}/{provider.model}"
            if self._cooldown_until.get(key, 0.0) > self._clock():
                failures.append(f"{key}: cooling down")
                continue
            try:
                result = await provider.complete(request)
            except ProviderError as exc:
                self._cooldown_until[key] = self._clock() + exc.cooldown_seconds
                logger.warning(
                    "LLM provider failed",
                    extra={
                        "provider": provider.name,
                        "model": provider.model,
                        "error": type(exc).__name__,
                        "cooldown_seconds": exc.cooldown_seconds,
                    },
                )
                failures.append(f"{key}: {type(exc).__name__}")
                continue
            self._cooldown_until.pop(key, None)
            return result
        raise AllProvidersFailedError(failures)
