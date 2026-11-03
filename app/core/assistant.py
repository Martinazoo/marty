"""Entry point for user interactions, independent of the chat channel."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.context import ContextBuilder
from app.core.orchestrator import Orchestrator, PendingConfirmation, TurnResult
from app.core.summaries import Summarizer
from app.core.tools import Decision, ToolContext, ToolExecutor
from app.database.models import ActionStatus, Message, MessageRole, User, UserPreferences
from app.services.conversations import ConversationService
from app.services.users import UserService

logger = logging.getLogger(__name__)

NO_LLM_TEXT = (
    "Ahora mismo no tengo ningún modelo de IA configurado, así que solo puedo responder a "
    "comandos. Escribe /help para verlos."
)
DECISION_NOTES = {
    ActionStatus.EXECUTED: "el usuario la ha confirmado y ya está hecha",
    ActionStatus.REJECTED: "el usuario la ha cancelado; no se ha cambiado nada",
    ActionStatus.EXPIRED: "ha caducado sin confirmar; no se ha cambiado nada",
    ActionStatus.FAILED: "el usuario la ha confirmado, pero ha fallado al ejecutarse",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Sender:
    telegram_user_id: int
    chat_id: int
    display_name: str | None


@dataclass
class Reply:
    text: str
    confirmations: list[PendingConfirmation] = field(default_factory=list)
    conversation_id: int | None = None
    new_conversation: bool = False


class Assistant:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        executor: ToolExecutor,
        orchestrator: Orchestrator | None,
        summarizer: Summarizer | None,
        context_builder: ContextBuilder,
        timezone: str,
        idle_gap: timedelta,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._sessions = session_factory
        self._executor = executor
        self._orchestrator = orchestrator
        self._summarizer = summarizer
        self._context = context_builder
        self._timezone = timezone
        self._idle_gap = idle_gap
        self._clock = clock

    def _conversations(self, session: AsyncSession) -> ConversationService:
        return ConversationService(session, idle_gap=self._idle_gap)

    async def _ensure_user(self, session: AsyncSession, sender: Sender) -> tuple[User, bool]:
        return await UserService(session).ensure(
            telegram_user_id=sender.telegram_user_id,
            telegram_chat_id=sender.chat_id,
            display_name=sender.display_name,
            timezone=self._timezone,
        )

    async def register(self, sender: Sender) -> bool:
        """Create or refresh the user. Returns True on first contact."""
        async with self._sessions() as session, session.begin():
            _, created = await self._ensure_user(session, sender)
        return created

    async def handle_text(self, sender: Sender, text: str, telegram_message_id: int) -> Reply:
        now = self._clock()
        # Stored in its own transaction, so the message survives a failure below.
        async with self._sessions() as session, session.begin():
            user, _ = await self._ensure_user(session, sender)
            conversations = self._conversations(session)
            conversation, is_new = await conversations.current(user.id, now)
            message = await conversations.add(
                conversation,
                Message(
                    role=MessageRole.USER,
                    content=text,
                    telegram_message_id=telegram_message_id,
                ),
                now,
            )
            user_id, conversation_id, message_id = user.id, conversation.id, message.id

        async with self._sessions() as session, session.begin():
            user = await UserService(session).get(user_id)
            conversations = self._conversations(session)
            conversation = await conversations.get(conversation_id)
            if self._orchestrator is None:
                turn = TurnResult(text=NO_LLM_TEXT)
            else:
                messages = await self._context.build(
                    session,
                    user=user,
                    conversation=conversation,
                    conversations=conversations,
                    user_text=text,
                    now=now,
                )
                turn = await self._orchestrator.run_turn(
                    session,
                    user=user,
                    conversation=conversation,
                    conversations=conversations,
                    messages=messages,
                    message_id=message_id,
                    now=now,
                )
            await conversations.add(
                conversation,
                Message(
                    role=MessageRole.ASSISTANT,
                    content=turn.text,
                    llm_provider=turn.provider,
                    llm_model=turn.model,
                    input_tokens=turn.input_tokens,
                    output_tokens=turn.output_tokens,
                ),
                self._clock(),
            )

        return Reply(
            text=turn.text,
            confirmations=turn.confirmations,
            conversation_id=conversation_id,
            new_conversation=is_new,
        )

    async def upkeep(self, reply: Reply) -> None:
        """Best-effort summaries, run after the reply has been sent."""
        if self._summarizer is None or reply.conversation_id is None:
            return
        try:
            async with self._sessions() as session, session.begin():
                conversations = self._conversations(session)
                conversation = await conversations.get(reply.conversation_id)
                now = self._clock()
                if reply.new_conversation:
                    previous = await conversations.previous(conversation)
                    if previous is not None:
                        await self._summarizer.update(conversations, previous, now, complete=True)
                await self._summarizer.update(conversations, conversation, now)
        except Exception:
            logger.exception("Conversation upkeep failed")

    async def decide(self, telegram_user_id: int, action_id: int, *, approve: bool) -> Decision:
        async with self._sessions() as session, session.begin():
            user = await UserService(session).get_by_telegram_id(telegram_user_id)
            if user is None:
                return Decision(None, None)
            now = self._clock()
            ctx = ToolContext(session=session, user=user, now=now)
            decision = await self._executor.decide(ctx, action_id, approve=approve)
            if decision.changed and decision.status in DECISION_NOTES:
                # Button presses bypass the LLM; record them so the next turn knows.
                conversations = self._conversations(session)
                conversation, _ = await conversations.current(user.id, now)
                note = DECISION_NOTES[decision.status]
                await conversations.add(
                    conversation,
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=f"(Solicitud «{decision.explanation}»: {note}.)",
                    ),
                    now,
                )
            return decision

    async def preferences(self, sender: Sender) -> UserPreferences:
        async with self._sessions() as session, session.begin():
            user, _ = await self._ensure_user(session, sender)
            return await UserService(session).preferences(user.id)

    async def pause(self, sender: Sender, duration: timedelta | None) -> datetime | None:
        until = self._clock() + duration if duration else None
        async with self._sessions() as session, session.begin():
            user, _ = await self._ensure_user(session, sender)
            await UserService(session).pause_notifications(user.id, until)
        return until

    async def resume(self, sender: Sender) -> None:
        async with self._sessions() as session, session.begin():
            user, _ = await self._ensure_user(session, sender)
            await UserService(session).resume_notifications(user.id)
