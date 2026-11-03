"""Telegram update handlers. They stay thin: all logic lives in `Assistant`."""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.core.assistant import Assistant, Reply, Sender
from app.core.context import WEEKDAYS_ES, format_local
from app.database.models import UserPreferences
from app.integrations.telegram import texts
from app.integrations.telegram.formatting import escape, parse_duration, split_message

logger = logging.getLogger(__name__)

CALLBACK_RE = re.compile(r"^act:(\d+):(yes|no)$")
UNAUTHORIZED_NOTICE_INTERVAL_SECONDS = 600
TYPING_REFRESH_SECONDS = 4.5


@asynccontextmanager
async def typing(chat: Chat) -> AsyncIterator[None]:
    """Keep the 'typing...' indicator alive (Telegram clears it after ~5 s)."""

    async def refresh() -> None:
        while True:
            with suppress(TelegramError):
                await chat.send_action(ChatAction.TYPING)
            await asyncio.sleep(TYPING_REFRESH_SECONDS)

    task = asyncio.create_task(refresh())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _sender(update: Update) -> Sender:
    user, chat = update.effective_user, update.effective_chat
    if user is None or chat is None:  # the gatekeeper lets no such update through
        raise ValueError("update without user or chat")
    return Sender(telegram_user_id=user.id, chat_id=chat.id, display_name=user.first_name)


class TelegramHandlers:
    def __init__(
        self,
        assistant: Assistant,
        *,
        allowed_user_ids: Collection[int],
        timezone: ZoneInfo,
    ) -> None:
        self._assistant = assistant
        self._allowed = frozenset(allowed_user_ids)
        self._tz = timezone
        self._last_unauthorized_notice: dict[int, float] = {}

    def register(self, application: Application) -> None:
        application.add_handler(TypeHandler(Update, self.gatekeeper), group=-1)

        private = filters.ChatType.PRIVATE & filters.User(user_id=self._allowed)
        for command, callback in (
            ("start", self.start),
            ("help", self.help),
            ("settings", self.settings),
            ("pause", self.pause),
            ("resume", self.resume),
        ):
            application.add_handler(CommandHandler(command, callback, filters=private))
        application.add_handler(MessageHandler(private & filters.COMMAND, self.unknown_command))
        application.add_handler(MessageHandler(private & filters.TEXT, self.text))
        application.add_handler(MessageHandler(private, self.unsupported))
        application.add_handler(CallbackQueryHandler(self.decision, pattern=CALLBACK_RE))
        application.add_error_handler(self.error)

    # --- Authorisation ---------------------------------------------------------

    async def gatekeeper(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Runs before every other handler. Stops anything that is not the allowed
        user in a private chat; nothing from strangers is processed or stored."""
        user, chat = update.effective_user, update.effective_chat
        is_private = chat is not None and chat.type == ChatType.PRIVATE
        if user is not None and user.id in self._allowed and is_private:
            return

        if chat is not None and not is_private:
            with suppress(TelegramError):
                await chat.leave()
        elif user is not None and self._should_notice(user.id):
            logger.warning("Unauthorised Telegram user", extra={"telegram_user_id": user.id})
            if update.callback_query is not None:
                with suppress(TelegramError):
                    await update.callback_query.answer(texts.PRIVATE_BOT)
            elif chat is not None:
                with suppress(TelegramError):
                    await chat.send_message(texts.PRIVATE_BOT)
        raise ApplicationHandlerStop

    def _should_notice(self, telegram_user_id: int) -> bool:
        """Rate limit replies and log lines, so a stranger cannot flood either."""
        now = time.monotonic()
        last = self._last_unauthorized_notice.get(telegram_user_id)
        if last is not None and now - last < UNAUTHORIZED_NOTICE_INTERVAL_SECONDS:
            return False
        self._last_unauthorized_notice[telegram_user_id] = now
        return True

    # --- Commands --------------------------------------------------------------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        sender = _sender(update)
        await self._assistant.register(sender)
        name = f", {sender.display_name}" if sender.display_name else ""
        await self._send(update, texts.WELCOME.format(name=escape(name)))

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send(update, texts.HELP)

    async def settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        preferences = await self._assistant.preferences(_sender(update))
        await self._send(update, self._render_preferences(preferences))

    async def pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        duration = None
        if args:
            duration = parse_duration(" ".join(args))
            if duration is None:
                await self._send(update, escape(texts.PAUSE_USAGE))
                return
        until = await self._assistant.pause(_sender(update), duration)
        message = (
            texts.PAUSED_UNTIL.format(until=format_local(until, self._tz))
            if until
            else texts.PAUSED_INDEFINITELY
        )
        await self._send(update, escape(f"{message}\n\n{texts.NO_PROACTIVE_YET}"))

    async def resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._assistant.resume(_sender(update))
        await self._send(update, escape(f"{texts.RESUMED}\n\n{texts.NO_PROACTIVE_YET}"))

    async def unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send(update, escape(texts.UNKNOWN_COMMAND))

    async def unsupported(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send(update, escape(texts.UNSUPPORTED_CONTENT))

    # --- Conversation ----------------------------------------------------------

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, chat = update.effective_message, update.effective_chat
        if message is None or message.text is None or chat is None:
            return
        async with typing(chat):
            reply = await self._assistant.handle_text(
                _sender(update), message.text, message.message_id
            )
        await self._send_reply(chat, reply)
        await self._assistant.upkeep(reply)

    async def _send_reply(self, chat: Chat, reply: Reply) -> None:
        for chunk in split_message(reply.text):
            await chat.send_message(escape(chunk), parse_mode=ParseMode.HTML)
        for confirmation in reply.confirmations:
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            texts.CONFIRM_BUTTON, callback_data=f"act:{confirmation.action_id}:yes"
                        ),
                        InlineKeyboardButton(
                            texts.CANCEL_BUTTON, callback_data=f"act:{confirmation.action_id}:no"
                        ),
                    ]
                ]
            )
            await chat.send_message(
                f"{texts.CONFIRMATION_HEADER}\n{escape(confirmation.explanation)}",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )

    async def decision(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        match = CALLBACK_RE.match(query.data or "")
        if match is None:
            await query.answer()
            return
        decision = await self._assistant.decide(
            query.from_user.id, int(match.group(1)), approve=match.group(2) == "yes"
        )
        if decision.status is None:
            await query.answer(texts.ACTION_NOT_FOUND)
            with suppress(BadRequest):
                await query.edit_message_reply_markup(None)
            return
        outcome = texts.DECISION_STATUS[decision.status]
        await query.answer(outcome if decision.changed else texts.ALREADY_DECIDED)
        with suppress(BadRequest):  # "message is not modified" on repeated presses
            await query.edit_message_text(
                f"{escape(decision.explanation or '')}\n\n<b>{escape(outcome)}</b>",
                parse_mode=ParseMode.HTML,
            )

    # --- Errors ----------------------------------------------------------------

    async def error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error(
            "Telegram update failed",
            exc_info=context.error,
            extra={"error": type(context.error).__name__},
        )
        if not isinstance(update, Update) or update.effective_user is None:
            return
        chat = update.effective_chat
        if update.effective_user.id in self._allowed and chat is not None:
            with suppress(TelegramError):
                await chat.send_message(texts.ERROR)

    # --- Helpers ---------------------------------------------------------------

    async def _send(self, update: Update, html_text: str) -> None:
        if update.effective_chat is not None:
            await update.effective_chat.send_message(html_text, parse_mode=ParseMode.HTML)

    def _render_preferences(self, p: UserPreferences) -> str:
        now = datetime.now(UTC)
        if not p.notifications_paused or (p.paused_until is not None and p.paused_until <= now):
            notifications = "activos"
        elif p.paused_until is not None:
            notifications = f"en pausa hasta el {format_local(p.paused_until, self._tz)}"
        else:
            notifications = "en pausa hasta que escribas /resume"
        weekday = WEEKDAYS_ES[p.weekly_report_weekday - 1]
        quiet = (
            f"de {p.quiet_hours_start:%H:%M} a {p.quiet_hours_end:%H:%M}"
            if p.quiet_hours_start and p.quiet_hours_end
            else "no configuradas"
        )
        briefing = (
            f"a las {p.morning_briefing_time:%H:%M}"
            if p.morning_briefing_enabled
            else "desactivado"
        )
        report = (
            f"{weekday} a las {p.weekly_report_time:%H:%M}"
            if p.weekly_report_enabled
            else "desactivado"
        )
        lines = [
            "<b>Preferencias</b>",
            f"Avisos proactivos: {escape(notifications)}",
            f"Resumen matinal: {briefing}",
            f"Informe semanal: {report}",
            f"Aviso si no registras estudio: tras {p.study_inactivity_days} días",
            f"Horas de silencio: {quiet}",
            "",
            escape(texts.NO_PROACTIVE_YET) + " Entonces podrás cambiar estos valores.",
        ]
        return "\n".join(lines)
