"""Adapter for OpenAI-compatible chat-completions APIs (Gemini, Groq, Ollama).

Response bodies are never logged: they contain the user's conversation.
"""

import json
import logging
import re
from typing import Any

import httpx

from app.integrations.ai.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResult,
    InvalidRequestError,
    ProviderUnavailableError,
    RateLimitedError,
    Role,
    ToolCall,
    Usage,
)

logger = logging.getLogger(__name__)

AUTH_COOLDOWN_SECONDS = 900.0
MAX_COOLDOWN_SECONDS = 24 * 3600.0
_GOOGLE_RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)s$")
_STANDARD_TOOL_CALL_KEYS = frozenset({"id", "type", "function", "index"})


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        client: httpx.AsyncClient,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        foreign_tool_call_fields: dict[str, Any] | None = None,
    ) -> None:
        """`foreign_tool_call_fields` are added to tool calls produced by another
        provider, for APIs that reject history without their own opaque fields."""
        self.name = name
        self.model = model
        self._key = f"{name}/{model}"
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout_seconds
        self._foreign_tool_call_fields = foreign_tool_call_fields

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [self._encode_message(m) for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
            body["tool_choice"] = "auto"

        response = await self._post("/chat/completions", body)
        return self._decode_completion(response)

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get(
                f"{self._base_url}/models", headers=self._headers, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.name}: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise self._error_for(response)
        return sorted(item["id"].removeprefix("models/") for item in response.json()["data"])

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        try:
            response = await self._client.post(
                f"{self._base_url}{path}", json=body, headers=self._headers, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(f"{self.name}: timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.name}: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise self._error_for(response)
        return response

    def _encode_message(self, message: ChatMessage) -> dict[str, Any]:
        encoded: dict[str, Any] = {"role": message.role.value}
        if message.content is not None:
            encoded["content"] = message.content
        if message.tool_calls:
            encoded["tool_calls"] = [self._encode_tool_call(call) for call in message.tool_calls]
            encoded.setdefault("content", None)
        if message.role is Role.TOOL:
            encoded["tool_call_id"] = message.tool_call_id
        return encoded

    def _encode_tool_call(self, call: ToolCall) -> dict[str, Any]:
        encoded: dict[str, Any] = {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }
        if call.provider == self._key:
            encoded.update(call.provider_fields or {})
        elif self._foreign_tool_call_fields:
            encoded.update(self._foreign_tool_call_fields)
        return encoded

    def _decode_completion(self, response: httpx.Response) -> CompletionResult:
        try:
            data = response.json()
            message = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailableError(f"{self.name}: malformed response") from exc

        tool_calls = tuple(
            self._decode_tool_call(index, raw)
            for index, raw in enumerate(message.get("tool_calls") or [])
        )
        content = _text_content(message.get("content"))
        if not content and not tool_calls:
            # Typically the output budget was spent on reasoning tokens.
            raise ProviderUnavailableError(f"{self.name}: empty response", cooldown_seconds=0)

        usage = data.get("usage") or {}
        return CompletionResult(
            message=ChatMessage(Role.ASSISTANT, content, tool_calls=tool_calls),
            provider=self.name,
            model=self.model,
            usage=Usage(usage.get("prompt_tokens"), usage.get("completion_tokens")),
        )

    def _decode_tool_call(self, index: int, raw: dict[str, Any]) -> ToolCall:
        function = raw.get("function") or {}
        arguments_raw = function.get("arguments") or "{}"
        try:
            arguments = (
                json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
            )
        except ValueError:
            arguments = None
        if not isinstance(arguments, dict):
            # Left for the tool executor to reject, so the model can correct itself.
            arguments = {"_unparseable_arguments": str(arguments_raw)[:500]}
        extra = {k: v for k, v in raw.items() if k not in _STANDARD_TOOL_CALL_KEYS}
        return ToolCall(
            id=raw.get("id") or f"call_{index}",
            name=function.get("name", ""),
            arguments=arguments,
            provider=self._key,
            provider_fields=extra or None,
        )

    def _error_for(self, response: httpx.Response) -> Exception:
        status = response.status_code
        error = _error_payload(response)
        code = str(error.get("code") or error.get("status") or "")
        message = str(error.get("message") or "")
        logger.info(
            "LLM provider returned an error",
            extra={"provider": self.name, "model": self.model, "status": status, "code": code},
        )
        label = f"{self._key}: HTTP {status} {code}".rstrip()

        if status in (429, 413):
            return RateLimitedError(label, cooldown_seconds=_retry_after(response, error))
        if status in (401, 403, 404) or "API key" in message:
            return ProviderUnavailableError(label, cooldown_seconds=AUTH_COOLDOWN_SECONDS)
        if status >= 500:
            return ProviderUnavailableError(label)
        if code == "tool_use_failed":
            # Groq: the model produced a malformed tool call. Not our bug.
            return ProviderUnavailableError(label, cooldown_seconds=0)
        if status == 400 and error.get("status") == "FAILED_PRECONDITION":
            return ProviderUnavailableError(label, cooldown_seconds=AUTH_COOLDOWN_SECONDS)
        return InvalidRequestError(f"{label}: {message[:300]}")


def _text_content(content: Any) -> str | None:
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


def _error_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return payload["error"]
    return {}


def _retry_after(response: httpx.Response, error: dict[str, Any]) -> float | None:
    seconds: float | None = None
    header = response.headers.get("retry-after")
    if header:
        try:
            seconds = float(header)
        except ValueError:
            seconds = None
    if seconds is None:
        for detail in error.get("details") or []:
            if isinstance(detail, dict) and detail.get("@type") == _GOOGLE_RETRY_INFO:
                match = _DURATION_RE.match(str(detail.get("retryDelay", "")))
                if match:
                    seconds = float(match.group(1))
    if seconds is None:
        return None
    return min(max(seconds, 1.0), MAX_COOLDOWN_SECONDS)
