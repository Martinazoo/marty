from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Conversation, Message, MessageRole
from app.database.repositories import ConversationRepository


class ConversationService:
    def __init__(self, session: AsyncSession, *, idle_gap: timedelta) -> None:
        self._session = session
        self._repo = ConversationRepository(session)
        self._idle_gap = idle_gap

    async def current(self, user_id: int, now: datetime) -> tuple[Conversation, bool]:
        """The ongoing conversation, or a new one after `idle_gap` of silence.

        The flag says whether the conversation was just created.
        """
        latest = await self._repo.latest(user_id)
        if latest is not None and now - latest.last_message_at < self._idle_gap:
            return latest, False
        return await self._repo.create(user_id, now), True

    async def get(self, conversation_id: int) -> Conversation:
        conversation = await self._session.get(Conversation, conversation_id)
        if conversation is None:
            raise LookupError(f"conversation {conversation_id} not found")
        return conversation

    async def previous(self, conversation: Conversation) -> Conversation | None:
        return await self._repo.previous(conversation.user_id, conversation.id)

    async def add(self, conversation: Conversation, message: Message, now: datetime) -> Message:
        conversation.last_message_at = max(conversation.last_message_at, now)
        message.created_at = now
        return await self._repo.add_message(conversation, message)

    async def recent(
        self, conversation: Conversation, limit: int, *, before_id: int | None = None
    ) -> list[Message]:
        return await self._repo.recent_messages(conversation.id, limit=limit, before_id=before_id)

    async def unsummarized(self, conversation: Conversation, *, up_to_id: int) -> list[Message]:
        return await self._repo.messages_between(
            conversation.id,
            after_id=conversation.summarized_through_message_id,
            up_to_id=up_to_id,
        )

    async def latest_message(self, conversation: Conversation) -> Message | None:
        messages = await self._repo.recent_messages(
            conversation.id, limit=1, roles=(MessageRole.USER, MessageRole.ASSISTANT)
        )
        return messages[0] if messages else None

    def store_summary(
        self, conversation: Conversation, summary: str, through_id: int, now: datetime
    ) -> None:
        conversation.summary = summary
        conversation.summarized_through_message_id = through_id
        conversation.summary_updated_at = now
