"""ASGI entry point. Run with: uvicorn --factory app.main:create_app"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import __version__
from app.agents import ALL_TOOLS
from app.agents.calendar import build_calendar_tools
from app.api.health import router as health_router
from app.core.assistant import Assistant
from app.core.config import Settings, get_settings
from app.core.context import ContextBuilder
from app.core.logging import configure_logging
from app.core.orchestrator import Orchestrator
from app.core.summaries import Summarizer
from app.core.tools import ToolExecutor, ToolRegistry
from app.database.session import create_engine, create_session_factory
from app.integrations.ai.factory import build_llm, build_transcriber
from app.integrations.apple_calendar import CalDAVClient
from app.integrations.telegram.bot import TelegramBot
from app.services.calendar import CalendarService

logger = logging.getLogger(__name__)


def build_calendar(settings: Settings, http_client: httpx.AsyncClient) -> CalendarService | None:
    if not settings.calendar_enabled or settings.caldav_password is None:
        logger.warning("Calendar disabled: CALDAV_USERNAME or CALDAV_PASSWORD unset")
        return None
    client = CalDAVClient(
        url=settings.caldav_url,
        username=settings.caldav_username,
        password=settings.caldav_password.get_secret_value(),
        client=http_client,
    )
    return CalendarService(client, settings.tz)


def build_assistant(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http_client: httpx.AsyncClient,
    calendar: CalendarService | None = None,
) -> Assistant:
    llm = build_llm(settings, http_client)
    tools = (*ALL_TOOLS, *(build_calendar_tools(calendar) if calendar else ()))
    registry = ToolRegistry(tools)
    executor = ToolExecutor(
        registry, pending_ttl=timedelta(hours=settings.pending_action_ttl_hours)
    )
    context_builder = ContextBuilder(calendar=calendar)
    return Assistant(
        session_factory=session_factory,
        executor=executor,
        orchestrator=Orchestrator(
            llm,
            registry,
            executor,
            max_iterations=settings.llm_max_tool_iterations,
            max_output_tokens=settings.llm_max_output_tokens,
        )
        if llm
        else None,
        summarizer=Summarizer(llm, keep_recent=context_builder.limits.recent_messages)
        if llm
        else None,
        context_builder=context_builder,
        timezone=settings.timezone,
        idle_gap=timedelta(hours=settings.conversation_idle_hours),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_engine(settings)
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)
    http_client = httpx.AsyncClient()
    calendar = build_calendar(settings, http_client)
    app.state.assistant = build_assistant(
        settings, app.state.session_factory, http_client, calendar
    )

    bot = (
        TelegramBot(
            settings,
            app.state.assistant,
            transcriber=build_transcriber(settings, http_client),
            calendar=calendar,
        )
        if settings.telegram_enabled
        else None
    )
    try:
        if bot is not None:
            await bot.start()
        logger.info(
            "MARTY started",
            extra={
                "version": __version__,
                "env": settings.app_env,
                "telegram": bot is not None,
                "calendar": calendar is not None,
            },
        )
        yield
    finally:
        if bot is not None:
            await bot.stop()
        await http_client.aclose()
        await engine.dispose()
        logger.info("MARTY stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    is_production = settings.app_env == "production"
    app = FastAPI(
        title="MARTY",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.include_router(health_router)
    return app
