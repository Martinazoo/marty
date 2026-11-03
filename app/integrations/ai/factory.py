import logging

import httpx

from app.core.config import Settings
from app.integrations.ai.fallback import FallbackChain
from app.integrations.ai.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Gemini 3 rejects tool calls without a thought signature. Google documents this
# placeholder for history that another model produced (fallback mid-turn).
GEMINI_FOREIGN_TOOL_CALL_FIELDS = {
    "extra_content": {"google": {"thought_signature": "skip_thought_signature_validator"}}
}


def build_providers(
    settings: Settings, client: httpx.AsyncClient
) -> list[OpenAICompatibleProvider]:
    """Providers from `AI_PROVIDER_ORDER` that have a model (and key, if needed) configured."""
    providers = []
    for name in settings.ai_provider_order:
        match name:
            case "gemini":
                key, model, base_url = (
                    settings.gemini_api_key,
                    settings.gemini_model,
                    GEMINI_BASE_URL,
                )
            case "groq":
                key, model, base_url = settings.groq_api_key, settings.groq_model, GROQ_BASE_URL
            case "ollama":
                key, model = None, settings.ollama_model
                base_url = settings.ollama_base_url.rstrip("/") + "/v1"
        needs_key = name != "ollama"
        if not model or (needs_key and key is None):
            logger.warning("AI provider skipped: not configured", extra={"provider": name})
            continue
        providers.append(
            OpenAICompatibleProvider(
                name=name,
                base_url=base_url,
                model=model,
                client=client,
                api_key=key.get_secret_value() if key else None,
                timeout_seconds=settings.llm_timeout_seconds,
                foreign_tool_call_fields=GEMINI_FOREIGN_TOOL_CALL_FIELDS
                if name == "gemini"
                else None,
            )
        )
    return providers


def build_llm(settings: Settings, client: httpx.AsyncClient) -> FallbackChain | None:
    providers = build_providers(settings, client)
    if not providers:
        logger.warning("No AI provider configured: free-form conversation is disabled")
        return None
    return FallbackChain(providers)
