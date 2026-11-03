"""Provider-agnostic LLM interface.

Business logic depends only on these types. Each provider adapter (Gemini,
Groq, Ollama, ...) translates them to and from its own wire format and maps
its failures onto the error classes below.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Opaque fields the provider requires to be sent back unchanged (Gemini's
    # thought signatures). Only echoed to the model that produced them, named in
    # `provider` as "<provider>/<model>".
    provider: str | None = None
    provider_fields: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool the model may request. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSpec, ...] = ()
    temperature: float = 0.3
    max_output_tokens: int = 1024


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class CompletionResult:
    message: ChatMessage
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, request: CompletionRequest) -> CompletionResult: ...


class ProviderError(Exception):
    """A provider failed in a way another provider might not."""

    default_cooldown_seconds: float = 30.0

    def __init__(self, message: str, *, cooldown_seconds: float | None = None) -> None:
        super().__init__(message)
        self.cooldown_seconds = (
            self.default_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        )


class RateLimitedError(ProviderError):
    """Quota or rate limit reached (HTTP 429 or equivalent)."""

    default_cooldown_seconds = 60.0


class ProviderUnavailableError(ProviderError):
    """Network failure, timeout, 5xx, unknown model or rejected credentials."""


class InvalidRequestError(Exception):
    """The request itself is wrong. Retrying elsewhere would hide a bug."""


class AllProvidersFailedError(Exception):
    def __init__(self, failures: list[str]) -> None:
        super().__init__("All LLM providers failed: " + "; ".join(failures))
        self.failures = failures
