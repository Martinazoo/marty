from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Conversation, Message, MessageRole


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest(self, user_id: int) -> Conversation | None:
        return await self._session.scalar(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
            .limit(1)
        )

    async def previous(self, user_id: int, before_id: int) -> Conversation | None:
        return await self._session.scalar(
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.id < before_id)
            .order_by(Conversation.id.desc())
            .limit(1)
        )

    async def previous_user_message(self, user_id: int, before_id: int) -> Message | None:
        """The user's message before `before_id`, across conversations."""
        return await self._session.scalar(
            select(Message)
            .where(
                Message.user_id == user_id,
                Message.role == MessageRole.USER,
                Message.id < before_id,
            )
            .order_by(Message.id.desc())
            .limit(1)
        )

    async def create(self, user_id: int, now: datetime) -> Conversation:
        conversation = Conversation(user_id=user_id, started_at=now, last_message_at=now)
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def add_message(self, conversation: Conversation, message: Message) -> Message:
        message.conversation_id = conversation.id
        message.user_id = conversation.user_id
        self._session.add(message)
        await self._session.flush()
        return message

    async def recent_messages(
        self,
        conversation_id: int,
        *,
        limit: int,
        roles: Collection[MessageRole] = (MessageRole.USER, MessageRole.ASSISTANT),
        before_id: int | None = None,
    ) -> list[Message]:
        """The latest `limit` messages, returned oldest first."""
        query = select(Message).where(
            Message.conversation_id == conversation_id, Message.role.in_(roles)
        )
        if before_id is not None:
            query = query.where(Message.id < before_id)
        rows = await self._session.scalars(query.order_by(Message.id.desc()).limit(limit))
        return list(reversed(rows.all()))

    async def messages_between(
        self,
        conversation_id: int,
        *,
        after_id: int | None,
        up_to_id: int,
        roles: Collection[MessageRole] = (MessageRole.USER, MessageRole.ASSISTANT),
    ) -> list[Message]:
        """Messages with `after_id < id <= up_to_id`, oldest first."""
        query = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.role.in_(roles),
            Message.id <= up_to_id,
        )
        if after_id is not None:
            query = query.where(Message.id > after_id)
        rows = await self._session.scalars(query.order_by(Message.id))
        return list(rows.all())
