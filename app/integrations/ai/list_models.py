"""List the models each configured AI provider offers to your API key.

    python -m app.integrations.ai.list_models
    docker compose exec backend python -m app.integrations.ai.list_models

A model being listed does not guarantee it is usable on the free tier: some
answer every request with HTTP 429 until billing is enabled.
"""

import asyncio

import httpx

from app.core.config import get_settings
from app.integrations.ai.base import ProviderError
from app.integrations.ai.factory import build_providers


async def main() -> None:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        configured = {
            "gemini": settings.gemini_model,
            "groq": settings.groq_model,
            "ollama": settings.ollama_model,
        }
        # One entry per provider; any model makes it eligible and listing does not use it.
        providers = build_providers(
            settings.model_copy(
                update={
                    "gemini_model": ["-"],
                    "groq_model": ["-"],
                    "ollama_model": settings.ollama_model[:1],
                }
            ),
            client,
        )
        for provider in providers:
            models = ", ".join(configured[provider.name]) or "-"
            print(f"== {provider.name} (configured: {models}) ==")
            try:
                for model in await provider.list_models():
                    print(f"  {model}")
            except ProviderError as exc:
                print(f"  error: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
