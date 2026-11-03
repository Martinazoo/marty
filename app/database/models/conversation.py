from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAtMixin, IdMixin, str_enum
from app.database.models.enums import Channel, MessageRole


class Conversation(IdMixin, Base):
    """A run of messages. A new one starts after `CONVERSATION_IDLE_HOURS` of silence."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_id_last_message_at", "user_id", "last_message_at"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    channel: Mapped[Channel] = mapped_column(
        str_enum(Channel, "channel"), server_default=Channel.TELEGRAM.value
    )
    started_at: Mapped[datetime]
    last_message_at: Mapped[datetime]
    summary: Mapped[str | None] = mapped_column(Text)
    summary_updated_at: Mapped[datetime | None]
    # Highest message id already folded into `summary`.
    summarized_through_message_id: Mapped[int | None] = mapped_column(BigInteger)


class Message(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_id_id", "conversation_id", "id"),)

    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole, "role"))
    content: Mapped[str] = mapped_column(Text)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    llm_provider: Mapped[str | None] = mapped_column(String(32))
    llm_model: Mapped[str | None] = mapped_column(String(128))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
