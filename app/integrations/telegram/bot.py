"""Bot lifecycle, driven from the FastAPI lifespan (long polling, no webhook)."""

import logging

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder

from app.core.assistant import Assistant
from app.core.config import Settings
from app.integrations.telegram.handlers import TelegramHandlers, Transcriber
from app.integrations.telegram.texts import COMMANDS

logger = logging.getLogger(__name__)

ALLOWED_UPDATES = [Update.MESSAGE, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER]


class TelegramBot:
    def __init__(
        self, settings: Settings, assistant: Assistant, transcriber: Transcriber | None = None
    ) -> None:
        if settings.telegram_bot_token is None:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        self._application: Application = (
            ApplicationBuilder().token(settings.telegram_bot_token.get_secret_value()).build()
        )
        TelegramHandlers(
            assistant,
            allowed_user_ids=settings.telegram_allowed_user_ids,
            timezone=settings.tz,
            transcriber=transcriber,
            voice_max_seconds=settings.voice_max_seconds,
        ).register(self._application)

    async def start(self) -> None:
        application = self._application
        await application.initialize()
        await application.bot.set_my_commands(
            [BotCommand(command, description) for command, description in COMMANDS]
        )
        await application.start()
        if application.updater is None:
            raise RuntimeError("PTB application has no updater")
        await application.updater.start_polling(allowed_updates=ALLOWED_UPDATES)
        logger.info("Telegram bot started", extra={"bot_username": application.bot.username})

    async def stop(self) -> None:
        application = self._application
        if application.updater is not None and application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        await application.shutdown()
        logger.info("Telegram bot stopped")
